# CTFCUP transmitter - cryptanalytic decoder

Run: python3 solve_chirp.py capture.wav
Or with demodulated octal text: python3 solve_chirp.py capture.wav.octal.txt --octal

No seed or input flag required. The PRNG state is recovered from exposed bits over GF(2).

Verified with transmitter, fixed all-zero seed (22-byte payload) and 00112233445566778899aabbccddeeff (43-byte payload), both CRC32 identical.

Decode pipeline: chirp demod => invert tone table => RS(7,3) GF(8) => recover 128-bit xorshift state from 2 leaked bits per symbol => reconstruct data bits => validate CRC32.
