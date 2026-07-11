"""Job recovery - handles jobs that were interrupted by service restarts.

On startup, finds any jobs that were in an active processing state and
resets them for retry, ensuring no work is permanently lost due to
unexpected shutdowns.
"""

import logging

from sqlalchemy import select

from app.database import async_session_factory
from app.models.job import Job, JobStatus

logger = logging.getLogger(__name__)

# States that indicate a job was actively being processed
IN_PROGRESS_STATES = [
    JobStatus.RESOLVING,
    JobStatus.SEARCHING,
    JobStatus.DOWNLOADING,
    JobStatus.PROCESSING,
]

# States that should be left alone during recovery
# REVIEWING needs human input, so we leave it
# APPROVED was explicitly approved, so re-queue it for download
APPROVED_STATE = JobStatus.APPROVED


class JobRecovery:
    """Recovers interrupted jobs on service startup.

    Scans for jobs in active processing states and resets them to PENDING
    so the pipeline will pick them up again. Respects the retry limit -
    if a job has already hit max retries, it's marked as FAILED instead.
    """

    MAX_RETRIES = 3

    async def recover(self) -> int:
        """Run recovery for all interrupted jobs.

        Called once during application startup.

        Returns:
            The number of jobs recovered (reset to PENDING or re-queued).
        """
        recovered_count = 0

        async with async_session_factory() as session:
            # Find jobs that were in progress when the service stopped
            result = await session.execute(
                select(Job).where(Job.status.in_(IN_PROGRESS_STATES))
            )
            interrupted_jobs = list(result.scalars().all())

            # Find approved jobs that haven't started downloading yet
            approved_result = await session.execute(
                select(Job).where(Job.status == APPROVED_STATE)
            )
            approved_jobs = list(approved_result.scalars().all())

            if not interrupted_jobs and not approved_jobs:
                logger.info("No interrupted jobs found during recovery")
                return 0

            # Process interrupted jobs
            for job in interrupted_jobs:
                job.retry_count += 1

                if job.retry_count >= self.MAX_RETRIES:
                    job.status = JobStatus.FAILED
                    job.error_message = (
                        f"Failed after {job.retry_count} attempts "
                        f"(interrupted during {job.status.value})"
                    )
                    logger.warning(
                        "Job permanently failed during recovery",
                        extra={
                            "job_id": str(job.id),
                            "previous_status": job.status.value,
                            "retry_count": job.retry_count,
                        },
                    )
                else:
                    previous_status = job.status.value
                    job.status = JobStatus.PENDING
                    job.error_message = (
                        f"Recovered from interrupted state: {previous_status}"
                    )
                    recovered_count += 1
                    logger.info(
                        "Job recovered and re-queued",
                        extra={
                            "job_id": str(job.id),
                            "previous_status": previous_status,
                            "retry_count": job.retry_count,
                        },
                    )

            # Re-queue approved jobs (they just need to continue from download)
            for job in approved_jobs:
                # Approved jobs go back to PENDING so the pipeline
                # re-processes them; the pipeline will detect the approval
                # and skip straight to download
                job.status = JobStatus.PENDING
                job.error_message = "Recovered: re-queued approved job after restart"
                recovered_count += 1
                logger.info(
                    "Approved job re-queued after restart",
                    extra={"job_id": str(job.id)},
                )

            await session.commit()

        logger.info(
            "Job recovery complete",
            extra={
                "recovered": recovered_count,
                "interrupted": len(interrupted_jobs),
                "approved_requeued": len(approved_jobs),
            },
        )

        return recovered_count

    async def get_recovery_summary(self) -> dict:
        """Get a summary of the current job state (useful for health checks).

        Returns:
            Dict with counts of jobs in each state.
        """
        async with async_session_factory() as session:
            summary = {}
            for status in JobStatus:
                result = await session.execute(
                    select(Job).where(Job.status == status)
                )
                jobs = list(result.scalars().all())
                summary[status.value] = len(jobs)

            return summary
