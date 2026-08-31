#!/usr/bin/env bash

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macOS"
else
    OS="Linux"
fi

clear
echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════╗"
echo "║     Instagram Reel Unliker - Setup Utility     ║"
echo "╚════════════════════════════════════════════════╝"
echo -e "${NC}"
echo

echo -e "${BLUE}[*] Checking Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[!] python3 not found${NC}"
    if [ "$OS" = "macOS" ]; then
        echo -e "${YELLOW}    Install it with: brew install python3${NC}"
    else
        echo -e "${YELLOW}    Install it with your package manager, e.g. apt install python3 python3-venv${NC}"
    fi
    exit 1
fi

python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" || {
    echo -e "${RED}[!] Python 3.10 or higher required${NC}"
    exit 1
}

if ! python3 -m pip --version &> /dev/null; then
    echo -e "${RED}[!] pip is not available to python3${NC}"
    if [ "$OS" = "Linux" ]; then
        echo -e "${YELLOW}    Install it with your package manager, e.g. apt install python3-pip${NC}"
    fi
    exit 1
fi
echo -e "${GREEN}[✓] Python check passed ($(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'))${NC}"

echo -e "${BLUE}[*] Setting up virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv || {
        echo -e "${RED}[!] Failed to create virtual environment${NC}"
        if [ "$OS" = "Linux" ]; then
            echo -e "${YELLOW}    You may need: apt install python3-venv${NC}"
        fi
        exit 1
    }
fi

source venv/bin/activate || {
    echo -e "${RED}[!] Failed to activate virtual environment${NC}"
    echo -e "${YELLOW}    If venv/ is stale, delete it and re-run: rm -rf venv && ./run.sh${NC}"
    exit 1
}
echo -e "${GREEN}[✓] Virtual environment activated${NC}"

echo -e "${BLUE}[*] Installing dependencies...${NC}"
python -m pip install --upgrade pip --quiet
python -m pip install --no-cache-dir -r requirements.txt || {
    echo -e "${RED}[!] Failed to install Python dependencies${NC}"
    exit 1
}

# ensta pulls these in but never reaches them; instagram_unliker.py stubs the imports. See README.
python -m pip uninstall -y -q moviepy imageio imageio-ffmpeg numpy pillow pyquery lxml \
    proglog python-dotenv decorator cssselect
echo -e "${GREEN}[✓] All dependencies installed successfully${NC}"
echo

echo -e "${CYAN}[*] Starting Instagram Reel Unliker...${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

python instagram_unliker.py
exit_code=$?

deactivate

if [ $exit_code -ne 0 ]; then
    echo -e "${RED}[!] Program exited with errors${NC}"
    exit 1
fi

echo
echo -e "${GREEN}[✓] Program completed successfully${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
