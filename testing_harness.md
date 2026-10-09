Harness runs are live (Release 8.1)

You can now run your harness on our server, with our model, from "Our harness" → Submit for a run. It runs the exact commit on your saved branch against a fresh copy of your instance (your writes are thrown away) and shows pass/fail per task and a score. One run per team every 3 days, so check the format before you submit.

Working example + full instructions: https://github.com/theschoolofai/agentswitch-harness-example

Add agentswitch-harness.toml to your repo root: install = "pip install -r requirements.txt" # or "uv sync" run = "python -m harness.runner" # your command results = "results.json" # file your run writes instances = ["suryodaya"] # and/or "keystone" timeout_minutes = 20 # 1–30

Use what we provide — no passwords, no .env, no keys of your own: AGENTSWITCH_BASE_URL the instance to call (POST $AGENTSWITCH_BASE_URL/api/mcp) AGENTSWITCH_TOKEN your seat's token: "Authorization: Bearer $AGENTSWITCH_TOKEN" AGENTSWITCH_INSTANCE suryodaya or keystone (we run once per instance you list) OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL our model, OpenAI-compatible, tool calls supported. Use the openai package (OpenAI() picks these up), LiteLLM or any OpenAI-compatible client. Gemini/Anthropic SDKs won't work here.

Write results.json: {"tasks": [{"id": "t1", "title": "What it checks", "passed": true, "score": 1.0, "evidence": "short proof"}], "summary": "one line"} passed must be true or false; score 0–1 (optional); 1–200 tasks.

No internet while your harness runs (the install step can use PyPI/GitHub). Keep testing locally however you like — this is the official run.