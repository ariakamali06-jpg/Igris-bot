FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=7860 \
    HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

RUN useradd -m -u 1000 user

WORKDIR /home/user/app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user . .
RUN mkdir -p /home/user/app/data && chown -R user:user /home/user/app

USER user

EXPOSE 7860

CMD ["python", "main.py"]
