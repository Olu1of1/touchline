"""Build model-voiced commentary timelines ahead of time and save them in timelines/.

    python -m touchline.prebuild                 # the recommended matches
    python -m touchline.prebuild M059 M346       # specific matches

Needs the Azure OpenAI settings in .env. Each match makes about 150 short co-commentary calls,
all checked by the Verifier, so expect a few minutes per match. The saved files include the
full colour log (every draft, every verdict), so the Verifier's work can be audited in the repo.
"""

import argparse
import json
import os
import time

from .llm import get_backend
from .pipeline import SHIPPED, build
from .server import RECOMMENDED


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("match_ids", nargs="*", default=RECOMMENDED)
    a = ap.parse_args()
    backend = get_backend()
    if backend is None:
        raise SystemExit("No language model configured. Fill in the AZURE_OPENAI_* values in .env first.")
    os.makedirs(SHIPPED, exist_ok=True)
    for mid in [m.upper() for m in a.match_ids]:
        t0 = time.time()
        print(f"{mid}: building with {backend.name} ...", flush=True)
        tl = build(mid, backend)
        log = [c for c in tl["colour_log"] if "proposed" in c]
        acc = sum(c.get("verdict") == "accepted" for c in log)
        rej = sum(c.get("verdict") == "rejected" for c in log)
        retried = sum(c.get("attempts", 1) > 1 for c in log)
        errors = sum(c.get("verdict") == "error" for c in log)
        with open(os.path.join(SHIPPED, f"{mid}.json"), "w", encoding="utf-8") as f:
            json.dump(tl, f, ensure_ascii=False)
        print(f"  {len(log)} model lines: {acc} accepted ({retried} after a retry), {rej} rejected, {errors} errors. "
              f"{time.time() - t0:.0f}s")
        if errors:
            first = next(c for c in log if c.get("verdict") == "error")
            print(f"  first error: {first.get('problems')}")


if __name__ == "__main__":
    main()
