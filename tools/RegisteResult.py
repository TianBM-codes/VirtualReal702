from src.l3.core.config import settings
from src.l3.infra.registry_repo import RegistryRepo

repo = RegistryRepo(settings.registry_db_path)
repo.adopt_default_result_group(
    project_id="4",
    result_group="sensitivity_batch_1",
    display_name="sensitivity_batch_1",
    source_path=r"D:\WorkSpace\Temp\702Input\database\4\l1\results\sensitivity_batch_1\external__Sensitivity__SENSITIVITY_CLOUD.h5",
    source_file="external__Sensitivity__SENSITIVITY_CLOUD.h5",
)
print("done")