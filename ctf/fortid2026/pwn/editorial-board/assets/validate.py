#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import string
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import language_tool_python

MAX_INPUT = 900
MIN_INPUT = 640
MIN_WORDS = 90
MIN_SENTENCES = 4
ALLOWED = set(string.ascii_letters + " .,;:'-?!")
WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
SENTENCE_RE = re.compile(r"[.!?]")
MIN_SCORE = 88.0
MAX_ISSUES_PER_100 = 2.2
MAX_SPELLING_PER_100 = 0.0
MAX_REPETITION_RATIO = 0.1
MAX_AVG_SENTENCE_WORDS = 32.0
SPELLING_RULE_IDS = {"MORFOLOGIK_RULE_EN_US", "MORFOLOGIK_RULE_EN_GB"}
COMMON_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "he", "her", "his", "in", "is", "it", "its", "of", "on",
    "or", "our", "she", "so", "that", "the", "their", "this", "to", "was",
    "we", "with", "you", "your",
}


@dataclass
class Report:
    ok: bool
    reason: str
    score: float
    words: int
    sentences: int
    total_issues: int
    spelling_issues: int
    issues_per_100_words: float
    spelling_per_100_words: float
    repetition_ratio: float
    avg_sentence_words: float


def fail(reason: str, words: int = 0, sentences: int = 0) -> Report:
    return Report(False, reason, 0.0, words, sentences, 0, 0, 0.0, 0.0, 0.0, 0.0)


def structural(text: str):
    text = text.rstrip("\r\n")
    if len(text) < MIN_INPUT:
        return None, fail("too short")
    if len(text) > MAX_INPUT:
        return None, fail("too long")
    if any(ch not in ALLOWED for ch in text):
        return None, fail("bad character")
    if text[-1:] not in ".?!":
        return None, fail("must end as a sentence")

    words = [m.group(0).lower() for m in WORD_RE.finditer(text)]
    sentences = len(SENTENCE_RE.findall(text))
    if len(words) < MIN_WORDS:
        return None, fail("not enough words", len(words), sentences)
    if sentences < MIN_SENTENCES:
        return None, fail("not enough sentences", len(words), sentences)

    counts = {}
    for word in words:
        if word not in COMMON_WORDS:
            counts[word] = counts.get(word, 0) + 1
    repeated = sum(n - 1 for n in counts.values() if n > 1)
    repetition = repeated / max(1, len(words))
    avg_sentence = len(words) / max(1, sentences)
    if repetition > MAX_REPETITION_RATIO:
        return None, fail("too repetitive", len(words), sentences)
    if avg_sentence > MAX_AVG_SENTENCE_WORDS:
        return None, fail("sentences too long", len(words), sentences)
    return (text, words, sentences, repetition, avg_sentence), None


def is_spelling(match) -> bool:
    return (
        getattr(match, "ruleId", "") in SPELLING_RULE_IDS
        or getattr(match, "category", "") == "TYPOS"
        or getattr(match, "ruleIssueType", "") == "misspelling"
    )


def validate(text: str) -> Report:
    checked, error = structural(text)
    if error:
        return error
    text, words, sentences, repetition, avg_sentence = checked

    tool = language_tool_python.LanguageTool("en-US")
    matches = [m for m in tool.check(text)]

    spelling = [m for m in matches if is_spelling(m)]
    issue_density = len(matches) * 100.0 / len(words)
    spelling_density = len(spelling) * 100.0 / len(words)
    score = 100.0 - issue_density * 5.0 - spelling_density * 10.0 - repetition * 55.0
    score -= max(0.0, avg_sentence - 24.0) * 0.6
    score = round(max(0.0, min(100.0, score)), 2)

    ok = issue_density <= MAX_ISSUES_PER_100 and spelling_density <= MAX_SPELLING_PER_100 and score >= MIN_SCORE
    if issue_density > MAX_ISSUES_PER_100:
        reason = "too many grammar or style findings"
    elif spelling_density > MAX_SPELLING_PER_100:
        reason = "too many spelling findings"
    elif score < MIN_SCORE:
        reason = "editorial score below threshold"
    else:
        reason = "accepted by the anti-slop desk"

    return Report(
        ok,
        reason,
        score,
        len(words),
        sentences,
        len(matches),
        len(spelling),
        round(issue_density, 2),
        round(spelling_density, 2),
        round(repetition, 3),
        round(avg_sentence, 2),
    )


def read_text(args) -> str:
    if args.stdin:
        return sys.stdin.buffer.read().decode("latin-1")
    return Path(args.file).read_text(encoding="latin-1")


def line(report: Report) -> str:
    status = "ACCEPT" if report.ok else "REJECT"
    return (
        f"{status}: {report.reason} | score={report.score:.2f} | "
        f"issues={report.issues_per_100_words:.2f}/100 | "
        f"spelling={report.spelling_per_100_words:.2f}/100 | "
        f"repetition={report.repetition_ratio:.3f} | "
        f"avg_sentence={report.avg_sentence_words:.2f} | "
        f"words={report.words} | sentences={report.sentences}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--score-only", action="store_true")
    args = parser.parse_args()
    if not args.stdin and not args.file:
        raise SystemExit("provide a file or --stdin")

    report = validate(read_text(args))
    if not args.quiet:
        if args.json:
            print(json.dumps(asdict(report), indent=2, sort_keys=True))
        elif args.score_only:
            print(f"{report.score:.2f}")
        else:
            print(line(report))
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
