# Original solver in the retained evidence archive

The participant's `glacierctf_thin-ice_materials.zip` contains the complete working solver and helpers in `scripts/`:

| File | Purpose |
| --- | --- |
| `run_flag.py` | successful remote driver, PoW → instance → Git requests → checkpointing → flag |
| `powlib.py` | challenge launcher and kCTF sloth PoW |
| `rlib.py` | Git pkt-line transport, host routing and advertisements |
| `packutil.py` | Git PACK/REF_DELTA construction primitives |
| `thin_ice_solver.py` | core Git pack builder, transport helpers and earlier v1 oracle |
| `thin_ice_solver_v2.py` | weighted unresolved-delta oracle, end-to-end local test |
| `flagguess.py` | unsuccessful dictionary/leet-phrase experiment |
| `thin_ice_README.txt` | contemporaneous v1/v2 explanation |

These are the preserved **historical scripts**, not reconstructed algorithms. The final remote driver was `run_flag.py`, with the v2 counted-delta technique.

`run_flag.py` hardcodes paths under `/tmp/gigatool/thi/` and expects `thin_ice_solver.py` to be importable as `thin_ice_solver`. For private local reuse, adjust `sys.path` and `STATE` to your unpack directory or place files in the original layout. Remote service and PoW sessions were ephemeral and may no longer exist.

The repository includes the smaller [verify_git_chain.py](verify_git_chain.py), which proves the flag-to-OID consistency offline. The complete agent evidence bundle, including raw flags, log and original scripts, is not published here to avoid leaking full flags and historic instance identifiers.

Example inspection of the archived solver (not executed during write-up preparation):

```sh
unzip glacierctf_thin-ice_materials.zip 'scripts/*'
python3 scripts/thin_ice_solver_v2.py --local-test
```
