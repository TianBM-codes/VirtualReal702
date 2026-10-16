"""Optional integration: RUN_ABAQUS_HISTORY_TEST=1 python -m pytest ..."""
import os
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("RUN_ABAQUS_HISTORY_TEST") != "1", reason="requires Abaqus Python")
def test_real_odb_history(monkeypatch, tmp_path):
    from services.model_update.analysis import sensor_node_history_service as service

    # Store tensors in a rotated local basis. Request directions remain global.
    script = tmp_path / "create_odb.py"
    source = tmp_path / "history.odb"
    script.write_text('''from odbAccess import Odb
from abaqusConstants import *
import sys
odb = Odb(name="History", analysisTitle="History", description="API integration", path=sys.argv[1])
part = odb.Part(name="P", embeddedSpace=THREE_D, type=DEFORMABLE_BODY)
part.addNodes(nodeData=((1,0.,0.,0.),(2,1.,0.,0.),(3,0.,1.,0.),(4,0.,0.,1.)))
part.addElements(elementData=((1,1,2,3,4),), type="C3D4")
inst = odb.rootAssembly.Instance(name="P-1", object=part)
for index in range(2):
    step = odb.Step(name="Step-" + str(index+1), description="Static", domain=TIME, timePeriod=1.)
    for frame_index in range(2):
        frame = step.Frame(incrementNumber=frame_index, frameValue=float(frame_index), description="Frame")
        u = frame.FieldOutput(name="U", description="U", type=VECTOR)
        u.addData(position=NODAL, instance=inst, labels=(1,2,3,4), data=((1.,2.,3.),)*4)
        s = frame.FieldOutput(name="S", description="S", type=TENSOR_3D_FULL)
        s.addData(position=ELEMENT_NODAL, instance=inst, labels=(1,),
                  data=((10.,20.,30.,0.,0.,0.),)*4,
                  localCoordSystem=((0.,1.,0.),(-1.,0.,0.),(0.,0.,1.)))
        e = frame.FieldOutput(name="LE", description="LE", type=TENSOR_3D_FULL, isEngineeringTensor=ON)
        e.addData(position=ELEMENT_NODAL, instance=inst, labels=(1,), data=((.01,.02,.03,.04,0.,0.),)*4)
odb.save()
odb.close()
''', encoding="ascii")
    command = service.resolve_abaqus_command()
    created = subprocess.run([command, "python", str(script), str(source)], cwd=tmp_path,
                             capture_output=True, timeout=120)
    assert created.returncode == 0, created.stdout + created.stderr
    monkeypatch.setattr(service, "_resolve_source", lambda *args: (str(source), "default_result"))
    monkeypatch.setattr(service, "_load_matches", lambda pid, sensors: [dict(sensor,
        instance_name="P-1", node_label=1, coordinates=[0,0,0], distance=0) for sensor in sensors])
    try:
        result = service._extract_sensor_node_history(7, [
            {"name": "X", "direction": [1.,0.,0.]},
            {"name": "Y", "direction": [0.,1.,0.]},
            {"name": "XY", "direction": [2**-.5,2**-.5,0.]}], ["U", "S", "E"])
    except service.AppError as exc:
        pytest.fail(str(exc.details))
    assert len(result["sensors"][0]["steps"]) == 2
    for sensor, stress, displacement in zip(result["sensors"], [20.,10.], [1.,2.]):
        for step in sensor["steps"]:
            assert len(step["frames"]) == 2
            for frame in step["frames"]:
                fields = frame["fields"]
                assert all(field["status"] == "ok" for field in fields), fields
                assert fields[0]["values"][0]["projection"] == pytest.approx(displacement)
                assert fields[1]["values"][0]["projection"] == pytest.approx(stress)
                assert fields[2]["field"] == "LE"
    # Independently check the oblique projection against Abaqus's own result.
    angled = result["sensors"][2]["steps"][0]["frames"][0]["fields"]
    assert angled[1]["values"][0]["projection"] == pytest.approx(15.)
    assert angled[2]["values"][0]["projection"] == pytest.approx(.035)
