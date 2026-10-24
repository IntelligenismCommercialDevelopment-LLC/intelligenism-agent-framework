# Local environment setup

Create the virtual environment at the engine root and install dependencies:

```bash
cd IAF_AI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` covers the engine itself (`requests`, `flask`, `croniter`).
Two capabilities need one extra package each, installed only if you use them:

```bash
pip install PyMuPDF      # rasterize PDF pages to PNG before sending them as images
pip install httpx        # streaming responses (lib/llm_client.stream_llm)
```

Then run a dispatch from the shell root:

```bash
cd ..
python3 chain.py --dispatch template --agent core --message "Hello"
```
