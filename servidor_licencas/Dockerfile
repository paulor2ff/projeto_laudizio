# Servidor de Licenças — Dockerfile
# Alternativa de deploy para Fly.io, VPS com Docker, ou qualquer
# plataforma que prefira container a buildpack. Railway e Render
# também aceitam isto automaticamente se presente.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# A chave privada e o banco de dados NÃO devem ser copiados para a
# imagem em produção — usar volumes/variáveis de ambiente (ver DEPLOY.md)
RUN rm -f chave_privada_SERVIDOR.pem servidor_licencas.db admin_token.txt

EXPOSE 8100

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
