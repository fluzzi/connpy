# connpy v6.0.0b8 - Modern Network Automation Environment (Local Build)
FROM python:3.11-slim

LABEL description="Connpy: AI-Driven Network Automation & Intelligence Platform"

# Configuración de Terminal y Python
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TERM=xterm-256color

WORKDIR /app

# 1. Herramientas base del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    openssh-client \
    fzf \
    ncurses-bin \
    bash \
    procps \
    unzip \
    ca-certificates \
    gnupg \
    iputils-ping \
    telnet \
    && rm -rf /var/lib/apt/lists/*

# 2. Instalar Docker CLI (para el plugin de docker de connpy)
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && apt-get install -y docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

# 3. Instalar Kubectl (para el plugin de k8s de connpy)
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/$(dpkg --print-architecture)/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl

# 4. Instalar AWS CLI y Session Manager Plugin (Universal x86_64/ARM64)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then AWS_ARCH="x86_64"; else AWS_ARCH="aarch64"; fi && \
    curl "https://awscli.amazonaws.com/awscli-exe-linux-$AWS_ARCH.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && ./aws/install && rm -rf awscliv2.zip aws/ && \
    if [ "$ARCH" = "x86_64" ]; then \
        curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "ssm.deb"; \
    else \
        curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_arm64/session-manager-plugin.deb" -o "ssm.deb"; \
    fi && \
    dpkg -i ssm.deb && rm ssm.deb

# 5. Copiar código local e instalar dependencias
COPY . .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# 6. Configuración de persistencia
# Creamos la carpeta y el puntero .folder para que connpy use /config
RUN mkdir -p /config /root/.ssh /root/.config/conn && chmod 700 /root/.ssh && \
    echo -n "/config" > /root/.config/conn/.folder

# Punto de entrada directo a connpy
ENTRYPOINT ["conn"]
