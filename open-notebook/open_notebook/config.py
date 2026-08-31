import os

# ROOT DATA FOLDER
DATA_FOLDER = "./data"

# LANGGRAPH CHECKPOINT FILE
sqlite_folder = f"{DATA_FOLDER}/sqlite-db"
os.makedirs(sqlite_folder, exist_ok=True)
LANGGRAPH_CHECKPOINT_FILE = f"{sqlite_folder}/checkpoints.sqlite"

# UPLOADS FOLDER
UPLOADS_FOLDER = f"{DATA_FOLDER}/uploads"
os.makedirs(UPLOADS_FOLDER, exist_ok=True)

# PODCASTS FOLDER
# Matches the root that build_episode_output_dir() (commands/podcast_commands.py)
# creates episode directories under when called with DATA_FOLDER in production.
PODCASTS_FOLDER = f"{DATA_FOLDER}/podcasts"
os.makedirs(PODCASTS_FOLDER, exist_ok=True)

# EXTERNAL CHAT AGENT SERVICE (optional)
# When set, notebook chat requests are proxied to this external agent service
# (e.g. a deepagents-based extraction service) instead of using a locally
# provisioned chat model. Point it at the service root, e.g. "http://localhost:5060".
AGENT_SERVICE_URL = os.environ.get("OPEN_NOTEBOOK_AGENT_URL", "").strip() or None
# Bearer token sent to the agent service (optional; must match the service's
# AGENT_API_KEY when authentication is enabled there).
AGENT_SERVICE_TOKEN = os.environ.get("OPEN_NOTEBOOK_AGENT_TOKEN", "").strip() or None
# Timeout in seconds for agent service calls - deep agent runs can take minutes.
AGENT_SERVICE_TIMEOUT = float(os.environ.get("OPEN_NOTEBOOK_AGENT_TIMEOUT", "600"))

# TIKTOKEN CACHE FOLDER
# Reads TIKTOKEN_CACHE_DIR from the environment so Docker can redirect the cache
# to a path outside /data/ (which is typically volume-mounted and would hide the
# pre-baked encoding baked into the image at build time).
TIKTOKEN_CACHE_DIR = os.environ.get("TIKTOKEN_CACHE_DIR", "").strip() or f"{DATA_FOLDER}/tiktoken-cache"
os.makedirs(TIKTOKEN_CACHE_DIR, exist_ok=True)
