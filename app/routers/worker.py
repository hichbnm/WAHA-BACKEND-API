from fastapi import APIRouter, Depends, HTTPException, Path, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.models import models, schemas
from sqlalchemy import select, update, delete
from app.utils.auth import verify_admin_token

router = APIRouter()

@router.post("/workers", response_model=schemas.WorkerResponse)
async def register_worker(worker: schemas.WorkerCreate, db: AsyncSession = Depends(get_db), _: str = Depends(verify_admin_token)):
    """ Add new worker to the system (admin only) """
    # Check for duplicate URL
    result = await db.execute(select(models.Worker).where(models.Worker.url == worker.url))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Worker with this URL already exists.")
    db_worker = models.Worker(**worker.dict())
    db.add(db_worker)
    await db.commit()
    await db.refresh(db_worker)
    return db_worker

@router.get("/workers", response_model=list[schemas.WorkerResponse])
async def list_workers(db: AsyncSession = Depends(get_db), _: str = Depends(verify_admin_token)):
    """ List all registered workers (admin only) """
    result = await db.execute(select(models.Worker))
    return result.scalars().all()

@router.patch("/workers/{worker_id}", response_model=schemas.WorkerResponse)
async def update_worker(worker_id: int = Path(...), worker: schemas.WorkerUpdate = None, db: AsyncSession = Depends(get_db), _: str = Depends(verify_admin_token)):
    """ Update worker (admin only) """
    result = await db.execute(select(models.Worker).where(models.Worker.id == worker_id))
    db_worker = result.scalar_one_or_none()
    if not db_worker:
        raise HTTPException(status_code=404, detail="Worker not found.")
    update_data = worker.dict(exclude_unset=True)
    # Check for duplicate URL if url is being updated
    if "url" in update_data and update_data["url"] != db_worker.url:
        duplicate = await db.execute(select(models.Worker).where(models.Worker.url == update_data["url"]))
        if duplicate.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Worker with this URL already exists.")
    for key, value in update_data.items():
        setattr(db_worker, key, value)
    db.add(db_worker)
    await db.commit()
    await db.refresh(db_worker)
    return db_worker

@router.delete("/workers/cleanup", status_code=204)
async def cleanup_workers(db: AsyncSession = Depends(get_db), _: str = Depends(verify_admin_token)):
    """Remove all workers and their sessions (admin only)"""
    from sqlalchemy import delete as sa_delete
    from app.models.models import WAHASession, Worker
    await db.execute(sa_delete(WAHASession))
    await db.execute(sa_delete(Worker))
    await db.commit()
    return None
    

@router.delete("/workers/{worker_id}", status_code=204)
async def delete_worker(worker_id: int = Path(...), force: bool = Query(False), db: AsyncSession = Depends(get_db), _: str = Depends(verify_admin_token)):
    """Delete worker (admin only)"""
    from sqlalchemy import select, delete as sa_delete
    from app.models.models import WAHASession
    result = await db.execute(select(models.Worker).where(models.Worker.id == worker_id))
    db_worker = result.scalar_one_or_none()
    if not db_worker:
        raise HTTPException(status_code=404, detail="Worker not found.")
    session_result = await db.execute(select(WAHASession).where(WAHASession.worker_id == worker_id))
    wahasessions = session_result.scalars().all()
    if wahasessions:
        if not force:
            if len(wahasessions) > 1:
                raise HTTPException(status_code=400, detail="Cannot delete worker: multiple active sessions are assigned to this worker.")
            raise HTTPException(status_code=400, detail="Cannot delete worker: active sessions are assigned to this worker.")
        # Force delete: remove all sessions for this worker
        await db.execute(sa_delete(WAHASession).where(WAHASession.worker_id == worker_id))
        await db.commit()
    await db.delete(db_worker)
    await db.commit()
    return None

