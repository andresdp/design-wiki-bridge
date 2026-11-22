# Cross-source DeepEval dataset

`.dataset.json` is a single-turn DeepEval dataset derived from [`data/questions/cross_source_benchmark.md`](../../data/questions/cross_source_benchmark.md).

- 25 curated items are preserved, including 20 answerable questions and 5 abstention controls.
- 10 additional variants were generated with DeepEval 4.1.8 using `deepeval generate --method goldens`, then checked against their parent answers and cited source locators.
- The generator used model `gpt-5.5-2026-04-23`; the run produced 20 candidates and reported API cost `$0.866060`. Ten candidates were retained.
- Answerable items cover code-only, PDF-only, code + PDF, and code + Web sources. Each item's `additional_metadata` records its benchmark ID, source types, exact source locator text, and (for generated variants) parent item.
- The five controls explicitly limit the question to the supplied local code/PDF corpus and disable Web search. Their expected behavior is abstention.

The generated variants were seeded from the curated reference answers, not copied source passages. Their expected answers were reviewed against the parent benchmark answers and source locators. The final Goldens omit generator context so an evaluation run can score the application's own retrieval context rather than reuse the answer as context.

Load the dataset with DeepEval's documented API:

```python
from deepeval.dataset import EvaluationDataset

dataset = EvaluationDataset()
dataset.add_goldens_from_json_file(file_path="tests/eval/.dataset.json")
```

The project already has offline diagnostics for faithfulness and contextual recall over saved traces. This dataset does not enable those diagnostics or add a new eval test suite.
