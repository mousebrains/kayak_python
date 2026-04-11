"""Douglas-Peucker polyline simplification and geometry parsing."""


def parse_geom(geom_str: str) -> list[tuple[float, float]]:
    """Parse "lon lat,lon lat,..." text into [(lon, lat), ...] tuples."""
    if not geom_str or not geom_str.strip():
        return []
    points: list[tuple[float, float]] = []
    for pair in geom_str.split(","):
        parts = pair.strip().split()
        if len(parts) != 2:
            continue
        try:
            points.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return points


def _perpendicular_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Perpendicular distance from point to line segment start-end."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return float(((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5)
    # Normalize
    mag_sq = dx * dx + dy * dy
    u = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / mag_sq
    u = max(0, min(1, u))
    proj_x = start[0] + u * dx
    proj_y = start[1] + u * dy
    return float(((point[0] - proj_x) ** 2 + (point[1] - proj_y) ** 2) ** 0.5)


def simplify(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker polyline simplification (iterative)."""
    if len(points) <= 2:
        return list(points)

    # Iterative stack-based approach to avoid recursion limits
    keep = [False] * len(points)
    keep[0] = True
    keep[-1] = True

    stack = [(0, len(points) - 1)]
    while stack:
        start_idx, end_idx = stack.pop()
        max_dist = 0.0
        max_idx = start_idx
        for i in range(start_idx + 1, end_idx):
            dist = _perpendicular_distance(points[i], points[start_idx], points[end_idx])
            if dist > max_dist:
                max_dist = dist
                max_idx = i
        if max_dist > epsilon:
            keep[max_idx] = True
            stack.append((start_idx, max_idx))
            stack.append((max_idx, end_idx))

    return [p for i, p in enumerate(points) if keep[i]]
