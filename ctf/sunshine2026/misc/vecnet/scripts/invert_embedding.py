#!/usr/bin/env python3
"""VecNet - invert a ChromaDB embedding back to text with vec2text.

Input is either a full Chroma `/get` dump (JSON produced by
`chroma_dump.py --out`) or this repo's `assets/` directory (embeddings.npy +
documents.json, the actual vectors dumped from the challenge instance).

The script does two things:

  1. Validates the embedding space: it embeds the known plaintext
     `sunshinectf8_` with the corrector's embedder and compares it to the
     stored `magic_string` vector. On the challenge vectors this cosine is
     1.0000 - proof that the local model matches the target model.
  2. Runs the vec2text inversion (gtr-base corrector) with `num_steps` and
     `sequence_beam_width` taken from the challenge's webmail settings
     (4 and 5) and prints the inverted text - the password rule.

Usage:
    python3 invert_embedding.py assets/ [--steps 4] [--beam 5]
                                        [--record user_password_requirements]
    python3 invert_embedding.py vec_get.json

Environment used for the challenge:
    pip install vec2text==0.0.13 torch numpy
(vec2text pulls sentence-transformers/transformers. With transformers>=5 on
CPU-only boxes the compatibility shims below are required; on transformers 4.x
the plain API works without them.)
"""
import argparse
import json
import os
import sys
import time

import numpy as np


# ---- transformers>=5 / CPU-only compatibility shims ---------------------
def _apply_compat_shims():
    import transformers
    import transformers.modeling_utils as _tmu

    if int(transformers.__version__.split(".")[0]) < 5:
        return  # plain API path, shims not needed

    import torch
    import vec2text.models.inversion as _inv
    import vec2text.models.corrector_encoder as _corr_mod

    _tmu.get_torch_context_manager_or_global_device = lambda: torch.device("cpu")

    def _patched_led(model_name, lora=False):
        return transformers.AutoModelForSeq2SeqLM.from_pretrained(model_name)

    _inv.load_encoder_decoder = _patched_led

    def _with_tied_keys(orig_init):
        def new_init(self, config, *args, **kwargs):
            orig_init(self, config, *args, **kwargs)
            if not hasattr(self, "all_tied_weights_keys"):
                self.all_tied_weights_keys = {}

        return new_init

    _inv.InversionModel.__init__ = _with_tied_keys(_inv.InversionModel.__init__)
    if hasattr(_corr_mod, "CorrectorEncoderModel"):
        _corr_mod.CorrectorEncoderModel.__init__ = _with_tied_keys(
            _corr_mod.CorrectorEncoderModel.__init__
        )


_apply_compat_shims()

import torch  # noqa: E402
import vec2text  # noqa: E402


def load_input(path):
    """Returns (ids, documents, embeddings) from a dump json or assets dir."""
    if os.path.isdir(path):
        ids = open(os.path.join(path, "ids.txt")).read().split()
        docs = json.load(open(os.path.join(path, "documents.json")))["documents"]
        embs = np.load(os.path.join(path, "embeddings.npy"))
        return ids, docs, embs
    data = json.load(open(path))
    return data["ids"], data.get("documents"), np.array(data["embeddings"], dtype=np.float32)


def cos(a, b):
    a = a / (np.linalg.norm(a) + 1e-12)
    b = b / (np.linalg.norm(b) + 1e-12)
    return float(a @ b)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="assets/ directory or chroma dump .json")
    ap.add_argument("--steps", type=int, default=4,
                    help="vec2text num_steps (0 = single forward pass; beam is forced to 0)")
    ap.add_argument("--beam", type=int, default=5, help="sequence_beam_width")
    ap.add_argument("--record", default=None,
                    help="id to invert (default: the record with a null document)")
    ap.add_argument("--known-text", default="sunshinectf8_",
                    help="plaintext used for the model-space sanity check")
    args = ap.parse_args()

    ids, docs, embs = load_input(args.input)
    idx = {i: k for k, i in enumerate(ids)}
    target = args.record or next(
        (i for i, d in zip(ids, docs) if not d), ids[0]
    )
    print(f"records: {ids}", flush=True)
    print(f"target record: {target!r}", flush=True)

    print("loading gtr-base corrector ...", flush=True)
    t0 = time.time()
    corrector = vec2text.load_pretrained_corrector("gtr-base")
    # transformers>=5 may request non-fp32; force fp32 for CPU matmuls
    corrector.model = corrector.model.float()
    corrector.inversion_trainer.model = corrector.inversion_trainer.model.float()
    for _m in (corrector.model, corrector.inversion_trainer.model):
        _gc = _m.encoder_decoder.generation_config
        if getattr(_gc, "length_penalty", None) is None:
            _gc.length_penalty = 1.0
        if getattr(_gc, "num_beams", None) is None:
            _gc.num_beams = 1
    print(f"corrector loaded in {time.time() - t0:.1f}s", flush=True)

    # ---- 1. embedding-space sanity check -------------------------------
    tokenizer = corrector.tokenizer
    embedder = corrector.inversion_trainer.model.embedder
    enc = embedder if not hasattr(embedder, "encoder") else embedder.encoder
    toks = tokenizer([args.known_text], return_tensors="pt", max_length=128,
                     truncation=True, padding="max_length")
    with torch.no_grad():
        hs = enc(input_ids=toks["input_ids"], attention_mask=toks["attention_mask"]).last_hidden_state
        mask = toks["attention_mask"].unsqueeze(-1).float()
        pooled = (hs * mask).sum(dim=1) / mask.sum(dim=1)
    known_emb = pooled[0].numpy()
    magic_i = idx.get("magic_string")
    if magic_i is not None:
        print(f"cos(embed({args.known_text!r}), magic_string vec) = {cos(known_emb, embs[magic_i]):.4f} "
              "(1.0000 means the local model matches the target)", flush=True)

    # ---- 2. inversion --------------------------------------------------
    # vec2text asserts beam width 0 for the single-forward (zero-step) mode
    steps = args.steps if args.steps > 0 else None
    beam = args.beam if steps is not None else 0
    t0 = time.time()
    res = vec2text.invert_embeddings(
        embeddings=torch.from_numpy(embs[idx[target]]).unsqueeze(0),
        corrector=corrector,
        num_steps=steps,
        sequence_beam_width=beam,
    )
    print(f"[{target}] steps={steps} beam={beam} ({time.time() - t0:.0f}s):", flush=True)
    for line in res:
        print("   ", line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())