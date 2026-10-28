#!/usr/bin/env bash
# Reproduce the whole evidence matrix on a local seekdb instance.
set -euo pipefail
cd "$(dirname "$0")/.."

TABLE=${TABLE:-demo_kb}
EV=records/evidence
mkdir -p "$EV"

python scripts/build_index.py --corpus assets/sample/corpus.jsonl --table "$TABLE" > "$EV/build_$TABLE.json"

# 1) baseline, all four strategies
python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table "$TABLE" \
  --out "$EV/run_default" | tee "$EV/run_default.txt"

# 2) tokenizer ablation: same corpus, fulltext index without `with parser ik`
python scripts/build_index.py --corpus assets/sample/corpus.jsonl --table kb_no_ik --parser none \
  > "$EV/build_no_ik.json"
python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table kb_no_ik \
  --strategies vector,fulltext,hybrid_rrf --out "$EV/run_no_ik" | tee "$EV/run_no_ik.txt"

# 3) candidate-pool depth sweep
for d in 10 20 50 100; do
  python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table "$TABLE" \
    --depth "$d" --out "$EV/run_depth_$d" > /dev/null
  grep -H "recall@10" "$EV/run_depth_$d.md" | head -1
done

# 4) fusion weight sweep for the weighted strategy
for w in 0.0 0.3 0.5 0.7 1.0; do
  python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table "$TABLE" \
    --strategies vector,fulltext,hybrid_weighted --w-vec "$w" \
    --out "$EV/run_wvec_$w" > /dev/null
done
python - <<'PY'
import json, pathlib
for w in ("0.0","0.3","0.5","0.7","1.0"):
    p = pathlib.Path(f"records/evidence/run_wvec_{w}.json")
    if not p.exists():
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    row = d["summary"].get("hybrid_weighted", {})
    print(f"w_vec={w:<4} recall@10={row.get('recall@10')} MRR={row.get('MRR')} nDCG@10={row.get('nDCG@10')}")
PY

# 5) scalar filter: matched vs unmatched against the annotated set
python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table "$TABLE" \
  --filter category=hybrid --only-tag hybrid --strategies vector,fulltext,hybrid_weighted \
  --out "$EV/run_filter_matched" | tee "$EV/run_filter_matched.txt"
python scripts/evaluate.py --qrels assets/sample/qrels.jsonl --table "$TABLE" \
  --filter category=hybrid --strategies vector,fulltext,hybrid_weighted \
  --out "$EV/run_filter_unmatched" | tee "$EV/run_filter_unmatched.txt"
