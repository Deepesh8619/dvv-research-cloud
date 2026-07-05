#!/bin/bash

# DVV Research Cloud — Development Server Launcher

set -e

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$PROJECT_ROOT"

echo "🚀 DVV Research Cloud — Development Setup"
echo "=========================================="
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}⚠️  Python 3 not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Python 3 found"

# Check Node
if ! command -v node &> /dev/null; then
    echo -e "${YELLOW}⚠️  Node.js not found${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Node.js found"

# Install backend dependencies
echo ""
echo -e "${BLUE}Installing backend dependencies...${NC}"
pip install -q fastapi uvicorn sqlalchemy python-multipart pydantic 2>/dev/null || true

# Install frontend dependencies
echo -e "${BLUE}Installing frontend dependencies...${NC}"
cd web
npm install -q 2>/dev/null || true
cd ..

echo ""
echo -e "${GREEN}✅ Setup complete!${NC}"
echo ""
echo -e "${YELLOW}To run the development servers:${NC}"
echo ""
echo -e "  ${BLUE}Terminal 1 (Backend):${NC}"
echo "  python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000"
echo ""
echo -e "  ${BLUE}Terminal 2 (Frontend):${NC}"
echo "  cd web && npm run dev"
echo ""
echo -e "${YELLOW}Then visit:${NC}"
echo "  🔧 API Docs: http://localhost:8000/docs"
echo "  🌐 App: http://localhost:3000"
echo ""
echo -e "${GREEN}Happy coding! 🎉${NC}"
