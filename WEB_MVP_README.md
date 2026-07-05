# DVV Research Cloud — Web MVP

## Complete End-to-End Application

**Backend:** FastAPI + SQLite + Python orchestration  
**Frontend:** React + Tailwind CSS + Vite  
**Features:** Upload, Dashboard, Study Management, Approval Gates, Downloads

---

## Architecture

```
Browser (React SPA)
    ↓
    ├─ /upload → Upload page (drag-drop SPSS files)
    ├─ /studies → Dashboard (study list)
    ├─ /studies/:id → Study detail (status + approvals)
    └─ /studies/:id/crosstabs → Crosstabs viewer
    ↓
API Gateway (FastAPI)
    ↓
    ├─ POST /api/studies/upload
    ├─ GET /api/studies
    ├─ GET /api/studies/{id}
    ├─ POST /api/studies/{id}/approve/{gate}
    └─ GET /api/studies/{id}/download/{format}
    ↓
Backend Pipeline
    ├─ Orchestration (classify → QC → code → crosstabs → render)
    ├─ Database (SQLite with studies + approvals)
    └─ Storage (Parquet + Excel/PPT files)
```

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 16+
- npm or yarn

### 1. Install Dependencies

```bash
cd /Users/deepeshsharma/Desktop/AI/dvv-research-cloud

# Backend
pip install -q fastapi uvicorn sqlalchemy python-multipart pydantic

# Frontend
cd web && npm install && cd ..
```

### 2. Run Backend (Terminal 1)

```bash
python -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Visit **Swagger UI:** http://localhost:8000/docs

### 3. Run Frontend (Terminal 2)

```bash
cd web
npm run dev
```

Output:
```
  VITE v5.0.0  ready in XXX ms

  ➜  Local:   http://localhost:3000/
```

Visit **App:** http://localhost:3000

---

## Usage Flow

### Step 1: Upload a Study
1. Open http://localhost:3000
2. Click "Upload Study" in sidebar
3. Drag-drop `data/pharma_study.sav` or click to browse
4. Wait for processing (~2-3 seconds)
5. Automatically redirects to study detail page

### Step 2: Monitor Status
- Study detail page shows live status (poll every 2 seconds)
- See classification confidence, QC results, approval chain
- Status: classifying → qc_pending → approved → rendered → delivered

### Step 3: Approve Study
- If QC passed, click "Approve" button
- Approval recorded in approval chain with timestamp

### Step 4: Download Results
- Once rendered, download Excel + PowerPoint decks
- Excel: Professional crosstabs with significance letters
- PPT: One slide per table with AI-generated insights

---

## File Structure

```
dvv-research-cloud/
├── api/                          # FastAPI backend
│   ├── main.py                   # FastAPI app
│   ├── config.py                 # Settings
│   ├── db.py                     # SQLite ORM
│   ├── models.py                 # Pydantic schemas
│   └── routes/
│       └── studies.py            # Study endpoints
│
├── web/                          # React frontend
│   ├── public/
│   │   └── index.html
│   ├── src/
│   │   ├── index.tsx
│   │   ├── App.tsx
│   │   ├── api/
│   │   │   └── client.ts         # API communication
│   │   ├── components/
│   │   │   ├── Layout.tsx
│   │   │   └── LoadingSpinner.tsx
│   │   ├── pages/
│   │   │   ├── UploadPage.tsx
│   │   │   ├── DashboardPage.tsx
│   │   │   ├── StudyPage.tsx
│   │   │   └── CrosstabsPage.tsx
│   │   └── styles/
│   │       └── globals.css
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── tailwind.config.js
│
├── src/                          # Core Python backend (existing)
│   ├── orchestration/
│   ├── engines/
│   ├── renderers/
│   └── adapters/
│
└── data/
    └── pharma_study.sav          # Sample data
```

---

## API Endpoints

### Studies
```
POST   /api/studies/upload              Upload SPSS file, start processing
GET    /api/studies                     List all studies
GET    /api/studies/{id}                Get study detail + status
DELETE /api/studies/{id}                Archive study
```

### Approvals
```
POST   /api/studies/{id}/approve/{gate}  Approve classification, QC, or delivery
```

### Downloads
```
GET    /api/studies/{id}/download/excel Download Excel deck
GET    /api/studies/{id}/download/pptx  Download PPT deck
GET    /api/studies/{id}/download/both  ZIP of both formats
```

---

## UI/UX Features

### Dark Professional Theme
- Primary color: RGB(31, 42, 68) — matches Excel headers
- Status indicators: Color-coded by workflow stage
- Responsive: Works on desktop + tablet

### Pages

**1. Upload Page**
- Drag-drop file zone
- File type validation
- Processing indicators
- Auto-redirect to study detail

**2. Dashboard**
- Table of all studies
- Sort/filter by status
- Quick actions (view, delete)
- Pagination support

**3. Study Detail**
- Status timeline (visual progress)
- QC results + classification confidence
- Approval chain with timestamps
- Conditional approval buttons
- Download buttons (Excel + PPT)

**4. Crosstabs Viewer**
- List of generated tables
- Interactive table display
- Hover tooltips (Z-scores, bases, sig letters)
- Placeholder for Phase 3.2 enhancements

---

## Environment Variables

Create `.env` file in project root:

```bash
# Backend
DATABASE_URL=sqlite:///./dvv_research.db
AUTO_APPROVE_CLASSIFY=true
AUTO_APPROVE_QC=true
REQUIRE_DELIVERY_APPROVAL=true

# API
API_TITLE=DVV Research Cloud
LOG_LEVEL=INFO

# Frontend (in web/.env)
VITE_API_URL=http://localhost:8000/api
```

---

## Deployment

### Local Testing
Already set up for development with hot reloading:
- Backend: `uvicorn api.main:app --reload`
- Frontend: `npm run dev`

### Staging (Railway/Render)
```dockerfile
FROM python:3.12
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
RUN cd web && npm install && npm run build
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Production
- Use PostgreSQL (multi-tenant with row-level security)
- Use Redis (job queue + caching)
- Use S3 (Excel/PPT storage)
- Deploy frontend to CloudFront
- Use managed FastAPI service (AWS Lambda, GCP Cloud Run, etc.)

---

## Testing

### Manual QA Checklist
- [ ] Upload SPSS file
- [ ] See real-time status updates
- [ ] Approve study
- [ ] Download Excel deck
- [ ] Download PPT deck
- [ ] Verify tables in Excel
- [ ] Verify slides in PPT with AI headlines

### Automated Tests (Coming Phase 3+)
```bash
# Backend tests
pytest tests/ -v

# Frontend tests
npm test -- --coverage
```

---

## Troubleshooting

**"Connection refused" when uploading**
- Ensure backend is running: `python -m uvicorn api.main:app --reload`
- Check port 8000 is available

**"Cannot find module" errors**
- Run `npm install` in `web/` directory
- Clear node_modules: `rm -rf web/node_modules && npm install`

**CORS errors**
- Backend already allows localhost:3000
- Check `api/config.py` for CORS_ORIGINS

**Slow uploads/processing**
- First time runs orchestration on file:
  - Classify: ~100ms
  - QC: ~100ms
  - Load data: ~50ms
  - Code verbatims: ~100ms (if open-ends present)
  - Crosstabs: ~150-200ms (7 tables)
  - Render Excel: ~40ms
  - Render PPT: ~100-200ms
- Total: 1-2 seconds typical

---

## Next Steps (Phase 3+)

### Week 2: Advanced Features
- [ ] Real crosstabs viewer with interactive tables
- [ ] Verbatim coding review UI
- [ ] Export to CSV/other formats
- [ ] Chart visualizations

### Week 3: Polish & Scale
- [ ] User authentication
- [ ] Multi-tenant support
- [ ] Advanced filtering & search
- [ ] Study comparison

### Beyond
- [ ] Mobile app
- [ ] API integrations (Slack, email, webhooks)
- [ ] Advanced analytics (trends, forecast)
- [ ] White-label SaaS
- [ ] Remote Ollama GPU hosting

---

## Support

- **Backend API Docs:** http://localhost:8000/docs
- **Frontend Issues:** Check browser console (F12)
- **Logs:** Backend prints to terminal, frontend to console

---

## Success Metrics

✅ MVP is ready when:
- File upload works end-to-end
- Study dashboard shows all studies
- Status updates in real-time
- Approval gates function
- Downloads work (Excel + PPT)
- No crashes during full workflow
- Response times <2s per operation

---

Build with ❤️ by Deepesh Sharma | Powered by FastAPI + React + Claude
