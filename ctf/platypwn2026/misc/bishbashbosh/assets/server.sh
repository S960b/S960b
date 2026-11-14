#!/usr/bin/env bash

files=(
  "/flag/flag.txt"
  "msg1"
  "msg2"
  "msg3"
)

echo "What message would you like to see? (0..2)"
read -r i
let i=$i-1

if (( i < 1 || i > 3 )); then
  echo "Nuh uh"
  exit 1
fi

file="${files[i]}"
cat -- "$file"
