# Cybersecurity Portfolio

**English** | [Русский](README.ru.md)

Cybersecurity / Penetration Testing / CTF

This repository contains my practical cybersecurity work: authorized security assessments and CTF writeups.

## Pentest

Authorized penetration testing and security assessment case studies.

- [API Security Assessment - GigaTool / MultiTool Gateway](pentest/gigatool/) - authorized black-box pentest of the backend API perimeter: token handling and DPoP binding, rate limiting, business logic and API issues
- [MultiTool Desktop Security Research (November 2026)](pentest/multitool-desktop/) - separate static/dynamic review of an Electron AI client: IPC, credential persistence, gateway and execution boundaries, network trust, and targeted fuzzing. Seven-point summary; potential issues are distinguished from verified impact.

## CTF

Writeups and technical notes from CTF competitions, structured as `ctf/<event>/<type>/<challenge>`.

- [CTF writeups](ctf/) - all competitions and categories: pwnsec, FortiD 2026, Sunshine 2026, pwnsec/web/PHault and more.


## Projects & infrastructure

- [Hermes Chat Bridge](hermes_chat_bridge/) — a Git-backed bridge connecting ChatGPT with a local AI agent (MultiTool / Hermes). It coordinates tasks, maintains per-task conversation history, tracks handoffs and displays activity in a local taskboard.

## Research

Independent, reproducible research into crypto-market data and trading hypotheses. Experiments are documented with methods, failure cases, limitations and available source code. **No verified strategy is presented as profitable or ready for live trading.** Research and simulations do not place real orders.

**Start here:** [Research archive and project index](research/README.en.md) · [Trading research audit (2026-11-10, Russian)](TRADING_RESEARCH_AUDIT_2026-11-10.md) · [Research validation protocol (Russian)](TRADING_RESEARCH_PROTOCOL.md).

| Project | Research focus | Recorded outcome |
|---|---|---|
| [TP/SL Lab](research/tpsl_lab/) | BTC/ETH entry, exit, TP/SL and aggressive trade flow | Archived; no robust net edge found in tested rules |
| [ETH Grid](research/eth_grid/) | ETH grid proxy versus ETH holding and a 50/50 portfolio | Closed / no-go after a separate frozen 7-day evaluation; all tested settings underperformed 50/50 |
| [Hyperliquid Wallets](research/hyperliquid_wallets/) | Public wallet fills, fees and realized PnL | Archived with incomplete trade/funding history; no valid whole-wallet return estimate |
| [NEWS Intelligence](research/) | News-driven BTC forecasts and market context | Historical phases documented; no independently verified profitable signal |
| [Binance Copy](research/binance_copy/) | Limits of inferring copy-trading returns from leader performance | Insufficient evidence of replicable follower returns |
| [SafeTrade](research/safetrade/) | Cross-venue price lead/lag and executable edge | Tested approaches did not establish a tradable edge |
| [Paper Seven-Day](research/paper_seven_day/) | Paper strategy runner and evaluation | Experimental; no claim of proven profitability |
| [Pionex](research/pionex/) | Exchange strategy investigation | Archived negative findings |

The archive distinguishes **negative results**, **incomplete data** and **unfinished evaluations**. The audit and validation protocol are methodological references, not trading signals. No real trades are opened by these research projects.

## Automation (personal daily ops)

- [LinkedIn daily posting](linkedin/) - autonomous daily LinkedIn post cycle (research,
  fact-gates, official Posts API publish, Telegram report) via Hermes cron + a resilient
  OS-cron wrapper. Includes the full from-zero restore guide. Personal identification is
  masked for the public repo.

## About

I am currently transitioning into cybersecurity after many years of technical experience with ISP networks and cryptocurrency mining.

My main interests are penetration testing, cybersecurity, CTF, Linux, networking and AI-assisted security research.