#!/bin/sh

check() {
  echo -e "\e[1;34m[+] Verifying Challenge Integrity\e[0m"
  sha256sum -c sha256sum
}

build_container() {
  echo -e "\e[1;34m[+] Building Challenge Docker Container\e[0m"
  docker build -t localhost/chall-more_power --platform linux/amd64 --pull=true   . 
}

run_container() {
  echo -e "\e[1;34m[+] Running Challenge Docker Container on 127.0.0.1:1337\e[0m"
  docker run --name chall-more_power --rm -p 127.0.0.1:1337:1337 -e HOST=127.0.0.1 -e PORT=1337 -e TIMEOUT=30 --user 1337:1337 --read-only --platform linux/amd64 --pull=never localhost/chall-more_power
}

kill_container() {
	docker ps --filter "name=chall-more_power" --format "{{.ID}}" \
		| tr '\n' ' ' \
		| xargs docker stop -t 0 \
		|| true
}

case "${1}" in
  "check")
    check
    ;;
  "build")
    build_container
    ;;
  "run")
    run_container
    ;;
  "kill")
    kill_container
    ;;
  *)
    check
    build_container && run_container
    ;;
esac
