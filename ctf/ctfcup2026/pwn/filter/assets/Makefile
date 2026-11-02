CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra -fno-stack-protector -no-pie
LDFLAGS ?= -lm

filter: filter.c model.h
	$(CC) $(CFLAGS) -o $@ filter.c $(LDFLAGS)

model.h: gen_model.py
	python3 gen_model.py

.PHONY: clean
clean:
	rm -f filter
