"""
Tool output compressor — uses Haiku to strip noise from security tool stdout.
Saves ~70% of tokens by passing digests to Analyzers instead of raw output.
"""

import os
import re
from typing import Optional

import anthropic

HAIKU = "claude-haiku-4-5"

COMPRESSION_PROMPTS: dict[str, str] = {
    "nmap": (
        "Extract from this nmap output: open ports with service/version, OS guess, "
        "any script findings. Skip closed/filtered ports and timing lines. "
        "Output as compact bullet list."
    ),
    "nuclei": (
        "Extract from this nuclei output: only lines with findings (skip INFO severity unless "
        "interesting, skip progress/stats lines). For each finding: template name, severity, "
        "matched-at URL, and extracted value if present. Compact format."
    ),
    "ffuf": (
        "Extract from this ffuf output: only lines where status != 404 and response size "
        "is not the baseline. List: URL, status code, size, words. Skip headers/progress."
    ),
    "sqlmap": (
        "Extract from this sqlmap output: confirmed injection points, payload used, "
        "database type/version if found, any extracted data. Skip banner/progress lines."
    ),
    "dalfox": (
        "Extract from this dalfox output: confirmed XSS findings with payload and parameter. "
        "Skip progress and non-POC lines."
    ),
    "nikto": (
        "Extract from this nikto output: security findings only. Skip server info lines, "
        "skip benign informational items. Focus on: dangerous files, outdated software, "
        "security misconfigurations."
    ),
    "whatweb": (
        "Extract from this whatweb output: technology stack detected. List: CMS, frameworks, "
        "server software, JavaScript libraries, version numbers where found."
    ),
    "testssl": (
        "Extract from this testssl.sh output: only findings rated MEDIUM, HIGH, or CRITICAL. "
        "Skip OK/LOW/INFO lines. Include: cipher suites, protocol issues, certificate issues."
    ),
    "feroxbuster": (
        "Extract from this feroxbuster output: discovered URLs with non-404 status codes. "
        "Include status code and content length. Skip 404s and rate-limit messages."
    ),
    "katana": (
        "Extract from this katana output: unique URLs discovered. Group by path depth. "
        "Skip duplicates. Flag any URLs with query parameters."
    ),
    "wapiti": (
        "Extract from this wapiti output: vulnerabilities found with severity and URL. "
        "Skip progress and informational lines."
    ),
    "default": (
        "Summarize this security tool output. Extract only actionable security findings: "
        "vulnerabilities, interesting endpoints, misconfigurations, credentials, or error messages "
        "that could indicate vulnerability. Skip progress bars, banners, and benign output. "
        "Be concise — one line per finding."
    ),
}


class ToolCompressor:
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=api_key)

    def compress(self, tool_name: str, raw_output: str, max_input_lines: int = 2000) -> dict:
        """Compress raw tool output to actionable digest. Returns compressed text + stats."""
        if not raw_output or not raw_output.strip():
            return {
                "compressed": "[empty output — investigate tool execution]",
                "original_lines": 0,
                "compressed_lines": 0,
                "ratio": 0.0,
                "tokens_saved_estimate": 0,
            }

        lines = raw_output.strip().splitlines()
        original_lines = len(lines)

        # Truncate very large outputs before sending to Haiku
        if len(lines) > max_input_lines:
            lines = lines[:max_input_lines]
            truncated = True
        else:
            truncated = False

        trimmed = "\n".join(lines)
        prompt = COMPRESSION_PROMPTS.get(tool_name.lower(), COMPRESSION_PROMPTS["default"])

        response = self.client.messages.create(
            model=HAIKU,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": f"{prompt}\n\n---TOOL OUTPUT---\n{trimmed}",
                }
            ],
        )

        compressed = response.content[0].text
        if truncated:
            compressed = f"[NOTE: output truncated to {max_input_lines} lines]\n{compressed}"

        compressed_lines = len(compressed.splitlines())
        original_tokens_estimate = len(trimmed.split()) * 1.3
        compressed_tokens_estimate = len(compressed.split()) * 1.3
        saved = max(0, int(original_tokens_estimate - compressed_tokens_estimate))

        return {
            "compressed": compressed,
            "original_lines": original_lines,
            "compressed_lines": compressed_lines,
            "ratio": round(compressed_lines / max(original_lines, 1), 3),
            "tokens_saved_estimate": saved,
        }

    def compress_phase_context(self, phase: int, raw_context: str) -> str:
        """Compress a full phase's findings + notes into a ~600-word handoff summary."""
        response = self.client.messages.create(
            model=HAIKU,
            max_tokens=900,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"You are summarizing Phase {phase} of a web application pentest for handoff "
                        f"to the next phase's agent. Write a structured summary covering: "
                        f"(1) endpoints discovered, (2) confirmed vulnerabilities with severity, "
                        f"(3) notable findings that need follow-up, (4) what was ruled out. "
                        f"Keep it under 700 words. Be precise — this is the only context the next "
                        f"agent will have about what was already tested.\n\n"
                        f"---PHASE {phase} DATA---\n{raw_context}"
                    ),
                }
            ],
        )
        return response.content[0].text

    def check_duplicate(self, new_finding: str, existing_findings: list[str]) -> dict:
        """Ask Haiku if a new finding is substantially duplicate of existing ones."""
        if not existing_findings:
            return {"is_duplicate": False, "confidence": 0.0, "matched_index": -1}

        existing_text = "\n---\n".join(
            f"[{i}] {f}" for i, f in enumerate(existing_findings)
        )

        response = self.client.messages.create(
            model=HAIKU,
            max_tokens=200,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Is the NEW FINDING substantially the same vulnerability as any EXISTING FINDING? "
                        "Same root cause + same endpoint = duplicate. Different endpoint = not duplicate. "
                        "Reply with JSON only: {\"is_duplicate\": bool, \"confidence\": 0.0-1.0, "
                        "\"matched_index\": int_or_-1, \"reason\": \"one sentence\"}\n\n"
                        f"NEW FINDING:\n{new_finding}\n\n"
                        f"EXISTING FINDINGS:\n{existing_text}"
                    ),
                }
            ],
        )

        import json
        try:
            text = response.content[0].text.strip()
            # Strip markdown code fences if present
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            return json.loads(text)
        except Exception:
            return {"is_duplicate": False, "confidence": 0.0, "matched_index": -1, "reason": "parse error"}
