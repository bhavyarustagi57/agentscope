from fastapi import APIRouter

from agentscope_api.api.routes.automatic_drift import (
    check_router as automatic_drift_check_router,
)
from agentscope_api.api.routes.automatic_drift import (
    configuration_router as automatic_drift_configuration_router,
)
from agentscope_api.api.routes.automatic_drift import (
    incident_router as monitoring_incident_router,
)
from agentscope_api.api.routes.bisection_analysis import router as bisection_analysis_router
from agentscope_api.api.routes.bisection_execution import router as bisection_execution_router
from agentscope_api.api.routes.bisections import router as bisections_router
from agentscope_api.api.routes.calibration import router as calibration_router
from agentscope_api.api.routes.drift import comparison_router as drift_comparison_router
from agentscope_api.api.routes.drift import policy_router as drift_policy_router
from agentscope_api.api.routes.evaluations import router as evaluations_router
from agentscope_api.api.routes.experiment_runs import router as experiment_runs_router
from agentscope_api.api.routes.experiments import router as experiments_router
from agentscope_api.api.routes.health import router as health_router
from agentscope_api.api.routes.judging import router as judging_router
from agentscope_api.api.routes.metrics import router as metrics_router
from agentscope_api.api.routes.monitoring import router as monitoring_router
from agentscope_api.api.routes.regressions import (
    checks_router as regression_checks_router,
)
from agentscope_api.api.routes.regressions import (
    router as regressions_router,
)
from agentscope_api.api.routes.traces import router as traces_router

router = APIRouter()
router.include_router(health_router)
router.include_router(metrics_router)
router.include_router(traces_router)
router.include_router(evaluations_router)
router.include_router(experiments_router)
router.include_router(experiment_runs_router)
router.include_router(calibration_router)
router.include_router(drift_policy_router)
router.include_router(drift_comparison_router)
router.include_router(judging_router)
router.include_router(regressions_router)
router.include_router(regression_checks_router)
router.include_router(bisections_router)
router.include_router(bisection_execution_router)
router.include_router(bisection_analysis_router)
router.include_router(monitoring_router)
router.include_router(automatic_drift_configuration_router)
router.include_router(automatic_drift_check_router)
router.include_router(monitoring_incident_router)
