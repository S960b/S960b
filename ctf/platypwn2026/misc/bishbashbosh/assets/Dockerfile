FROM alpine/socat
RUN apk add bash

RUN echo "Hello, my friend!" > /msg1
RUN echo "Hallo, mein Freund!" > /msg2
RUN echo "Hola, mi amigo!" > /msg3

COPY server.sh /

COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["TCP-LISTEN:9000,fork,reuseaddr", "EXEC:/server.sh"]
