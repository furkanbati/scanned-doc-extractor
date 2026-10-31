# Running the local-model experiments (E4, E5)

E4 (RapidOCR + a local LLM) and E5 (a local vision model, no OCR) run their
models through Ollama on the CPU. They need more memory than the other
experiments: `qwen3.5:4b` alone takes about 6 GB once loaded.

Give Docker at least 10 GB of memory (Docker Desktop: Settings > Resources).
With 7.5 GB the model runner was killed for lack of memory.

## 1. Start Ollama and pull the models

```bash
docker compose up -d ollama
docker compose exec -d ollama ollama pull qwen3.5:4b
docker compose exec -d ollama ollama pull qwen3-vl:2b
docker compose exec ollama ollama list
```

`exec -d` keeps a pull running when the terminal's connection to Docker drops;
a pull that stops part-way continues where it stopped when started again.

## 2. Run

```bash
docker compose run --rm test python -m evaluation.run --config e4
docker compose run --rm test python -m evaluation.run --config e5 --split dev --limit 20
docker compose run --rm test python -m evaluation.run --config e5
```

Each finished receipt is appended to `evaluation/results/<name>/checkpoint.jsonl`.
If a run stops, start the same command again: receipts already in the checkpoint
are not run again. The checkpoint is removed once the reports are written.

## Measured so far

| Run | Receipts | Mean field accuracy | s/receipt |
|---|---|---|---|
| E4, qwen3.5:4b, dev | first 20 | 0.713 | 36 (4096-token context) |
| E4, test | 101 of 400, stopped | - | 20 (3072-token context) |
| E5 | not run | - | - |
