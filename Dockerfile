FROM kalilinux/kali-rolling:latest

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    nmap nikto whatweb curl wget git python3 python3-pip \
    golang-go nodejs npm hydra wpscan sqlmap \
    dnsutils whois jq net-tools \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Go-based tools
ENV GOPATH=/root/go
ENV PATH=$PATH:/root/go/bin

RUN go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest && \
    go install github.com/projectdiscovery/katana/cmd/katana@latest && \
    go install github.com/projectdiscovery/httpx/cmd/httpx@latest && \
    go install github.com/ffuf/ffuf/v2@latest && \
    go install github.com/lc/gau/v2/cmd/gau@latest && \
    go install github.com/hahwul/dalfox/v2@latest && \
    go install github.com/jaeles-project/gospider@latest && \
    go install github.com/haccer/subjack@latest && \
    go install github.com/tomnomnom/qsreplace@latest

# Rust-based tools
RUN curl https://sh.rustup.rs -sSf | bash -s -- -y && \
    /root/.cargo/bin/cargo install feroxbuster

ENV PATH=$PATH:/root/.cargo/bin

# Python tools
RUN pip3 install --no-cache-dir \
    wafw00f \
    crlfuzz \
    wapiti3 \
    testssl

# testssl.sh
RUN git clone --depth 1 https://github.com/drwetter/testssl.sh.git /tools/testssl && \
    ln -s /tools/testssl/testssl.sh /usr/local/bin/testssl.sh

# sqlmap (latest)
RUN git clone --depth 1 https://github.com/sqlmapproject/sqlmap.git /tools/sqlmap && \
    ln -s /tools/sqlmap/sqlmap.py /usr/local/bin/sqlmap

# dalfox already installed via Go above

# commix
RUN git clone --depth 1 https://github.com/commixproject/commix.git /tools/commix && \
    ln -s /tools/commix/commix.py /usr/local/bin/commix

# SSTImap
RUN git clone --depth 1 https://github.com/vladko312/SSTImap.git /tools/SSTImap && \
    pip3 install --no-cache-dir -r /tools/SSTImap/requirements.txt

# smuggler
RUN git clone --depth 1 https://github.com/defparam/smuggler.git /tools/smuggler

# graphql-cop
RUN git clone --depth 1 https://github.com/dolevf/graphql-cop.git /tools/graphql-cop && \
    pip3 install --no-cache-dir -r /tools/graphql-cop/requirements.txt

# nosqlmap
RUN git clone --depth 1 https://github.com/codingo/NoSQLMap.git /tools/nosqlmap && \
    pip3 install --no-cache-dir -r /tools/nosqlmap/requirements.txt

# jwt_tool
RUN git clone --depth 1 https://github.com/ticarpi/jwt_tool.git /tools/jwt_tool && \
    pip3 install --no-cache-dir -r /tools/jwt_tool/requirements.txt

# websocat
RUN curl -L https://github.com/vi/websocat/releases/latest/download/websocat.x86_64-unknown-linux-musl \
    -o /usr/local/bin/websocat && chmod +x /usr/local/bin/websocat

# corsy (CORS scanner)
RUN git clone --depth 1 https://github.com/s0md3v/Corsy.git /tools/corsy && \
    pip3 install --no-cache-dir -r /tools/corsy/requirements.txt

# Update nuclei templates
RUN nuclei -update-templates 2>/dev/null || true

# Wordlists
RUN mkdir -p /wordlists && \
    curl -sL https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/common.txt \
    -o /wordlists/common.txt && \
    curl -sL https://raw.githubusercontent.com/danielmiessler/SecLists/master/Discovery/Web-Content/big.txt \
    -o /wordlists/big.txt && \
    curl -sL https://raw.githubusercontent.com/danielmiessler/SecLists/master/Usernames/top-usernames-shortlist.txt \
    -o /wordlists/users.txt && \
    curl -sL https://raw.githubusercontent.com/danielmiessler/SecLists/master/Passwords/Common-Credentials/10-million-password-list-top-1000.txt \
    -o /wordlists/passwords.txt

WORKDIR /engagements
CMD ["/bin/bash"]
