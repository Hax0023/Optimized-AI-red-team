"""
Burp Suite integration bridge.
Routes tool traffic through Burp proxy and drives active scanning via Burp REST API.
"""

import os
import socket
from typing import Optional

import requests

# Per-tool CLI proxy flags. {proxy_url} replaced at runtime.
TOOL_PROXY_FLAGS: dict[str, list[str]] = {
    "nuclei":      ["--proxy", "{proxy_url}"],
    "ffuf":        ["-x", "{proxy_url}"],
    "sqlmap":      ["--proxy={proxy_url}"],
    "dalfox":      ["--proxy", "{proxy_url}"],
    "feroxbuster": ["--proxy", "{proxy_url}"],
    "katana":      ["-proxy", "{proxy_url}"],
    "wapiti":      ["--proxy", "{proxy_url}"],
    "gobuster":    ["--proxy", "{proxy_url}"],
    "wfuzz":       ["-p", "{proxy_url}"],
    "curl":        ["-x", "{proxy_url}"],
    "httpx":       ["-proxy", "{proxy_url}"],
    "commix":      ["--proxy={proxy_url}"],
    "sstimap":     ["--proxy", "{proxy_url}"],
    "crlfuzz":     ["-p", "{proxy_url}"],
}

# Tools that use HTTP_PROXY env var rather than CLI flags
ENV_PROXY_TOOLS = {"gau", "hakrawler", "whatweb", "nikto", "wafw00f"}


class BurpBridge:
    def __init__(
        self,
        proxy_host: str = "127.0.0.1",
        proxy_port: int = 8080,
        api_port: int = 1337,
        api_key: str = "",
    ):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.proxy_url = f"http://{proxy_host}:{proxy_port}"
        self.api_base = f"http://{proxy_host}:{api_port}/v0.1"
        self.api_key = api_key
        self._session = requests.Session()
        if api_key:
            self._session.headers["X-Burp-Api-Key"] = api_key

    def is_proxy_alive(self, timeout: float = 3.0) -> bool:
        """TCP connect check — works without sending HTTP, avoids Burp intercept hang."""
        try:
            with socket.create_connection((self.proxy_host, self.proxy_port), timeout=timeout):
                return True
        except (OSError, ConnectionRefusedError):
            return False

    def is_api_alive(self, timeout: float = 3.0) -> bool:
        """Check Burp REST API. 401 = wrong key but API is up."""
        try:
            r = self._session.get(f"{self.api_base}/scan", timeout=timeout)
            return r.status_code in (200, 401)
        except Exception:
            return False

    def get_proxy_args(self, tool_name: str) -> list[str]:
        """Return proxy CLI flags for a tool, ready to append to its command."""
        flags = TOOL_PROXY_FLAGS.get(tool_name.lower(), [])
        return [f.replace("{proxy_url}", self.proxy_url) for f in flags]

    def get_proxy_env(self) -> dict[str, str]:
        """Return HTTP_PROXY env vars for tools that read from environment."""
        return {
            "HTTP_PROXY":  self.proxy_url,
            "HTTPS_PROXY": self.proxy_url,
            "http_proxy":  self.proxy_url,
            "https_proxy": self.proxy_url,
        }

    def needs_env_proxy(self, tool_name: str) -> bool:
        return tool_name.lower() in ENV_PROXY_TOOLS

    def start_active_scan(
        self,
        urls: list[str],
        scope_includes: list[str],
        scan_config: Optional[str] = None,
    ) -> dict:
        """Trigger a Burp active scan. Returns task_id on success."""
        payload: dict = {
            "scope": {
                "include": [{"rule": u} for u in scope_includes],
                "exclude": [],
            },
            "urls": urls,
        }
        if scan_config:
            payload["scan_configurations"] = [{"name": scan_config}]

        try:
            r = self._session.post(f"{self.api_base}/scan", json=payload, timeout=15)
            if r.status_code == 201:
                task_id = r.headers.get("Location", "").rstrip("/").split("/")[-1]
                return {"success": True, "task_id": task_id}
            return {"success": False, "error": r.text, "status_code": r.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_scan_status(self, task_id: str) -> dict:
        """Poll scan progress. status: running | succeeded | failed."""
        try:
            r = self._session.get(f"{self.api_base}/scan/{task_id}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                metrics = data.get("scan_metrics", {})
                return {
                    "task_id": task_id,
                    "status": data.get("scan_status", "unknown"),
                    "progress": metrics.get("crawl_and_audit_progress", 0),
                    "requests_made": metrics.get("requests_made", 0),
                    "issue_count": len(data.get("issue_events", [])),
                }
            return {"error": r.text, "status_code": r.status_code}
        except Exception as e:
            return {"error": str(e)}

    def get_scan_findings(self, task_id: str) -> list[dict]:
        """Pull all issue_events from a completed scan, deduplicated."""
        try:
            r = self._session.get(f"{self.api_base}/scan/{task_id}", timeout=20)
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []

        findings = []
        seen: set[str] = set()

        for event in data.get("issue_events", []):
            issue = event.get("issue", {})
            title = issue.get("name", "Unknown")
            url = issue.get("origin", "") + issue.get("path", "")
            dedup_key = f"{title}|{url}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            evidence_parts = []
            for ev in issue.get("evidence", []):
                if isinstance(ev, dict):
                    rr = ev.get("request_response", {})
                    if rr:
                        evidence_parts.append(
                            f"Request: {rr.get('url', url)}\n"
                            f"Snippet: {str(rr.get('response', ''))[:300]}"
                        )
            evidence = "\n".join(evidence_parts) or "See Burp Suite HTTP history for full request/response."

            findings.append({
                "title": title,
                "severity": self._map_severity(issue.get("severity", "info")),
                "confidence": issue.get("confidence", "tentative"),
                "endpoint": url,
                "description": issue.get("issue_background", issue.get("description", "")),
                "remediation": issue.get("remediation_background", ""),
                "evidence": evidence,
                "vuln_class": title,
                "source": "burp_active_scan",
            })

        return findings

    @staticmethod
    def _map_severity(burp_severity: str) -> str:
        return {
            "high":        "high",
            "medium":      "medium",
            "low":         "low",
            "info":        "info",
            "information": "info",
        }.get(burp_severity.lower(), "info")


def from_engagement_config(config: dict) -> Optional[BurpBridge]:
    """Reconstruct a BurpBridge from stored engagement config. None if Burp not enabled."""
    burp = config.get("burp")
    if not burp or not burp.get("enabled"):
        return None
    return BurpBridge(
        proxy_host=burp.get("proxy_host", os.getenv("BURP_HOST", "127.0.0.1")),
        proxy_port=burp.get("proxy_port", 8080),
        api_port=burp.get("api_port", 1337),
        api_key=burp.get("api_key", ""),
    )
