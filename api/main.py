"""
Discovery Engine API

FastAPI backend for the Discovery Engine web application.
Provides read access to signals.db and manages app.db for user metadata.

Run:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
"""

import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="Discovery Engine API",
    description="API for Discovery Engine - deal sourcing for Press On Ventures",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str


class VersionResponse(BaseModel):
    version: str
    api_version: str
    python_version: str


class CompanySummary(BaseModel):
    id: int
    canonical_key: str
    company_name: str
    confidence: float = 0.0
    signal_types: List[str] = Field(default_factory=list)
    status: Optional[str] = None
    last_signal_at: Optional[str] = None


class CompanyDetail(BaseModel):
    id: int
    canonical_key: str
    company_name: str
    website: Optional[str] = None
    confidence: float = 0.0
    signal_types: List[str] = Field(default_factory=list)
    status: Optional[str] = None
    why_now: Optional[str] = None
    created_at: str
    last_signal_at: Optional[str] = None


class FounderSummary(BaseModel):
    id: int
    founder_key: str
    name: str
    linkedin_url: Optional[str] = None
    current_title: Optional[str] = None
    founder_score: float = 0.0


class Signal(BaseModel):
    id: int
    signal_type: str
    source_api: str
    confidence: float
    detected_at: str
    created_at: str


class PaginatedResponse(BaseModel):
    items: List
    total: int
    page: int
    page_size: int
    has_more: bool


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc).isoformat(),
        version="1.0.0",
    )


@app.get("/version", response_model=VersionResponse, tags=["System"])
async def get_version():
    import platform
    return VersionResponse(
        version="1.0.0",
        api_version="v1",
        python_version=platform.python_version(),
    )


@app.get("/api/v1/companies", tags=["Companies"])
async def list_companies(
    q: Optional[str] = Query(None, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    sort: str = Query("confidence", description="Sort field"),
    status: Optional[str] = Query(None, description="Filter by status"),
):
    """
    List companies with pagination, search, and filtering.
    
    Returns companies from the signals database with aggregated signal data.
    """
    from storage.signal_store import SignalStore
    
    db_path = os.environ.get("SIGNALS_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    
    try:
        await store.initialize()
        
        offset = (page - 1) * page_size
        
        query = """
            SELECT 
                s.id,
                s.canonical_key,
                s.company_name,
                s.confidence,
                s.signal_type,
                p.status,
                s.created_at
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE 1=1
        """
        params = []
        
        if q:
            query += " AND (s.company_name LIKE ? OR s.canonical_key LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        
        if status:
            query += " AND p.status = ?"
            params.append(status)
        
        query += f" ORDER BY s.confidence DESC LIMIT ? OFFSET ?"
        params.extend([page_size, offset])
        
        cursor = await store._db.execute(query, params)
        rows = await cursor.fetchall()
        
        count_query = "SELECT COUNT(*) FROM signals"
        cursor = await store._db.execute(count_query)
        total = (await cursor.fetchone())[0]
        
        companies = []
        for row in rows:
            companies.append({
                "id": row[0],
                "canonical_key": row[1] or "",
                "company_name": row[2] or "Unknown",
                "confidence": row[3] or 0.0,
                "signal_types": [row[4]] if row[4] else [],
                "status": row[5],
                "last_signal_at": row[6],
            })
        
        return {
            "items": companies,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": offset + len(companies) < total,
        }
    finally:
        await store.close()


@app.get("/api/v1/companies/{company_id}", tags=["Companies"])
async def get_company(company_id: int):
    """Get detailed information about a specific company."""
    from storage.signal_store import SignalStore
    
    db_path = os.environ.get("SIGNALS_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    
    try:
        await store.initialize()
        
        cursor = await store._db.execute(
            """
            SELECT 
                s.id, s.canonical_key, s.company_name, s.confidence,
                s.signal_type, s.raw_data, s.created_at, s.detected_at,
                p.status
            FROM signals s
            LEFT JOIN signal_processing p ON s.id = p.signal_id
            WHERE s.id = ?
            """,
            (company_id,)
        )
        row = await cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Company not found")
        
        import json
        raw_data = json.loads(row[5]) if row[5] else {}
        
        return {
            "id": row[0],
            "canonical_key": row[1] or "",
            "company_name": row[2] or "Unknown",
            "website": raw_data.get("website", ""),
            "confidence": row[3] or 0.0,
            "signal_types": [row[4]] if row[4] else [],
            "status": row[8],
            "why_now": raw_data.get("why_now", ""),
            "created_at": row[6],
            "last_signal_at": row[7],
        }
    finally:
        await store.close()


@app.get("/api/v1/companies/{company_id}/founders", tags=["Companies"])
async def get_company_founders(company_id: int):
    """Get founders associated with a company."""
    from storage.signal_store import SignalStore
    from storage.founder_store import FounderStore
    
    signals_db = os.environ.get("SIGNALS_DB_PATH", "signals.db")
    signal_store = SignalStore(signals_db)
    founder_store = FounderStore(signals_db)
    
    try:
        await signal_store.initialize()
        await founder_store.initialize()
        
        cursor = await signal_store._db.execute(
            "SELECT canonical_key FROM signals WHERE id = ?",
            (company_id,)
        )
        row = await cursor.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Company not found")
        
        canonical_key = row[0]
        
        cursor = await founder_store._db.execute(
            """
            SELECT id, founder_key, name, linkedin_url, current_title, founder_score
            FROM founders
            WHERE canonical_key = ?
            """,
            (canonical_key,)
        )
        rows = await cursor.fetchall()
        
        founders = []
        for row in rows:
            founders.append({
                "id": row[0],
                "founder_key": row[1],
                "name": row[2],
                "linkedin_url": row[3],
                "current_title": row[4],
                "founder_score": row[5] or 0.0,
            })
        
        return {"founders": founders}
    finally:
        await signal_store.close()
        await founder_store.close()


@app.get("/api/v1/system/ingestion-status", tags=["System"])
async def get_ingestion_status():
    """Get the status of the last ingestion run."""
    from storage.signal_store import SignalStore
    
    db_path = os.environ.get("SIGNALS_DB_PATH", "signals.db")
    store = SignalStore(db_path)
    
    try:
        await store.initialize()
        
        cursor = await store._db.execute(
            "SELECT MAX(created_at) FROM signals"
        )
        row = await cursor.fetchone()
        
        cursor = await store._db.execute(
            "SELECT COUNT(*) FROM signals"
        )
        count_row = await cursor.fetchone()
        
        return {
            "last_signal_at": row[0] if row else None,
            "total_signals": count_row[0] if count_row else 0,
            "status": "healthy",
        }
    finally:
        await store.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
