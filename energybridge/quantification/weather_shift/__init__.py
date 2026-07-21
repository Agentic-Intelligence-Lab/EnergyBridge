"""Daily weather feature extraction reused by DR event memory retrieval.

`features.py` provides the daily weather feature vectors (temperature,
humidity, radiation, cloud cover) that `dr_event_memory.py` uses for its
weather-similarity retrieval term. This is intentionally scoped to feature
extraction only -- it does not include any distribution-shift/importance-
sampling correction logic, which is a separate, unrelated workstream.
"""
