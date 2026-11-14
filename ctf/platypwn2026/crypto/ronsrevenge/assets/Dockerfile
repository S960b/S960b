FROM python:3.14-alpine

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache socat

RUN adduser --disabled-password ctf
USER ctf

WORKDIR /challenge
COPY --chown=ctf:ctf server.py ./

EXPOSE 1337
CMD ["/usr/bin/socat", "TCP-LISTEN:1337,fork", "EXEC:python server.py"]
