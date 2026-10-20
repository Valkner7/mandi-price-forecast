"""Checks for the retraining quality gate in train_forecast_model.py.

Run:  python test_training_gate.py
Exit code 0 = all checks passed, 1 = at least one failure.

What is checked, and why:
  1. The gate helper accepts a model only when its backtest MAE is strictly
     lower than "price stays the same"; equal, worse, NaN or missing numbers
     are all refused.
  2. A model that loses to the baseline is NOT saved: main() returns the
     "rejected" exit code and the existing model files are byte-for-byte
     unchanged.
  3. A model that wins is saved as before (exit 0, files replaced).
  4. --force saves a losing model anyway.
  5. With no existing model at all, a losing model is still saved, so the
     service is never left with nothing to serve.

main() is run for real on the committed price data, with only the backtest
numbers faked (so the test does not depend on how good today's model is).
Files are written to a temporary folder, never to models/.
"""
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import train_forecast_model as tr

failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


print("[1] gate helper")
beats = tr._model_beats_baseline
check("lower MAE passes", beats({"model_mae": 100.0, "naive_mae": 101.0}))
check("equal MAE is refused", not beats({"model_mae": 100.0, "naive_mae": 100.0}))
check("higher MAE is refused", not beats({"model_mae": 185.7, "naive_mae": 180.9}))
check("NaN model MAE is refused", not beats({"model_mae": float("nan"), "naive_mae": 100.0}))
check("NaN naive MAE is refused", not beats({"model_mae": 100.0, "naive_mae": float("nan")}))
check("missing numbers are refused", not beats({}))
check("None is refused", not beats({"model_mae": None, "naive_mae": 100.0}))

real_backtest = tr.backtest_vs_naive


def fake_backtest(model_mae, naive_mae):
    def _fake(test, model):
        out = real_backtest(test, model)
        out["model_mae"], out["naive_mae"] = model_mae, naive_mae
        return out
    return _fake


def run_main(tmp, backtest, args=(), existing=True):
    """Run main() against files in tmp. Returns (exit code, model bytes, meta bytes)."""
    model_p, meta_p = tmp / "model.joblib", tmp / "meta.json"
    for p in (model_p, meta_p):
        if p.exists():
            p.unlink()
    if existing:
        model_p.write_bytes(b"OLD-MODEL")
        meta_p.write_bytes(b"OLD-META")
    tr.MODEL_PATH, tr.META_PATH = model_p, meta_p
    tr._previous_total_rows = lambda: None        # skip the "too few new rows" guard
    tr.backtest_vs_naive = backtest
    old_argv, sys.argv = sys.argv, ["train_forecast_model.py", *args]
    try:
        code = tr.main()
    finally:
        sys.argv = old_argv
    return (code,
            model_p.read_bytes() if model_p.exists() else None,
            meta_p.read_bytes() if meta_p.exists() else None)


with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)

    print("[2] losing model is rejected and the old files survive")
    code, model, meta = run_main(tmp, fake_backtest(185.7, 180.9))
    check("exit code is EXIT_MODEL_REJECTED", code == tr.EXIT_MODEL_REJECTED, str(code))
    check("rejected code is not 0 and not 1", code not in (0, 1))
    check("existing model file untouched", model == b"OLD-MODEL")
    check("existing meta file untouched", meta == b"OLD-META")

    print("[3] winning model is saved")
    code, model, meta = run_main(tmp, fake_backtest(100.0, 110.0))
    check("exit code 0", code == 0, str(code))
    check("model file replaced", model not in (None, b"OLD-MODEL"))
    check("meta file replaced", meta not in (None, b"OLD-META"))

    print("[4] --force saves a losing model")
    code, model, meta = run_main(tmp, fake_backtest(185.7, 180.9), args=("--force",))
    check("exit code 0", code == 0, str(code))
    check("model file replaced", model not in (None, b"OLD-MODEL"))

    print("[5] no existing model: a losing model is still saved")
    code, model, meta = run_main(tmp, fake_backtest(185.7, 180.9), existing=False)
    check("exit code 0", code == 0, str(code))
    check("model file written", model is not None)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED: " + "; ".join(failures))
    sys.exit(1)
print("All training-gate checks passed.")
