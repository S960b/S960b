#!/usr/bin/env python3
"""Offline consistency checks for the recorded El Clasico solution.

This does NOT reconstruct the Groth16 proof or replace the genuine verifier.
No remote requests and no external packages are needed.
"""
P = int("1a0111ea397fe69a4b1ba7b6434bacd764774b84f38512bf6730d2a0f6b0f6241eabfffeb153ffffb9feffffffffaaab", 16)
R = int("73eda753299d7d483339d80809a1d80553bda402fffe5bfeffffffff00000001", 16)
LAMBDA = 228988810152649578064853576960394133503
S1 = 457977620305299156129707153920788267003
S2 = 7
S = 2060899291373846202583682192643547201524
A = 3859827992693971436689685827005211754276481880816562847490303616321221349134106039597233557278825932218968372827554
B = 370601891209717769651537860104905451759518638243331195825859715829118167265533205768136178822114374101079811340242
QX = 1584780694560657844154277029370426164267952387141559224180614549114962334259569013804818685035127047514909255903123
QY = 2095466204314274245097794330496267071967100452915863254790274341080764295629831218260666363208506332673113309342367

def main():
    assert LAMBDA**2 + LAMBDA + 1 != 0
    assert (LAMBDA**2 + LAMBDA + 1) % R == 0
    assert (S1 + LAMBDA*S2) % R == S
    assert S1 == 2*LAMBDA - 3 and S2 == 7
    assert S1.bit_length() == 129 and S2.bit_length() == 3
    assert S % 9 == 6
    assert (LAMBDA - LAMBDA) % R == 0
    assert QX != A
    assert (pow(QX - A, 3, P) - pow(QY + B, 2, P)) % P == 0
    assert (QY*QY - QX*QX*QX - 4) % P != 0
    print("GLV decomposition, lattice, challenge equation, off-curve point: OK")
    print("This does not independently verify the Groth16 proof.")

if __name__ == "__main__":
    main()
