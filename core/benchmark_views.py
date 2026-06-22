from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from . import benchmarking as bm


@api_view(["GET"])
@permission_classes([IsAdminUser])
def benchmark_stats(request):
    return JsonResponse(bm.get_all_stats(), safe=False)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def benchmark_report(request):
    return JsonResponse(bm.generate_report(), safe=False)


@api_view(["POST"])
@permission_classes([IsAdminUser])
def benchmark_reset(request):
    bm.reset_all()
    return JsonResponse({"message": "Benchmark data reset."})


@api_view(["POST"])
@permission_classes([IsAdminUser])
def benchmark_snapshot(request):
    name = request.data.get("name") or f"snapshot_{len(bm.list_snapshots()) + 1}"
    bm.take_snapshot(name)
    return JsonResponse({"message": f"Snapshot '{name}' taken.", "snapshot": name})


@api_view(["GET"])
@permission_classes([IsAdminUser])
def benchmark_snapshots_list(request):
    return JsonResponse({"snapshots": bm.list_snapshots()})


@api_view(["POST"])
@permission_classes([IsAdminUser])
def benchmark_compare(request):
    before = request.data.get("before")
    after = request.data.get("after")
    if not before or not after:
        return JsonResponse({"error": "Both 'before' and 'after' snapshot names required."}, status=400)
    rows = bm.compare_snapshots(before, after)
    return JsonResponse({"comparison": rows, "before": before, "after": after}, safe=False)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def benchmark_bottleneck(request):
    return JsonResponse(bm.identify_bottleneck(), safe=False)
