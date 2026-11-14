FROM python:3.14-alpine

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache socat
RUN pip install --no-cache-dir pycryptodome

RUN adduser --disabled-password ctf
USER ctf

WORKDIR /challenge
COPY --chown=ctf:ctf server.py encryption.py ./

EXPOSE 2004
CMD ["/usr/bin/socat", "TCP-LISTEN:2004,fork", "EXEC:python server.py"]
