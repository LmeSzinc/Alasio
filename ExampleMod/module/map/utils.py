"""
The area helpers the recognition modules use.

They are plain tuple arithmetic, the same as the game script `module/base/utils.py`
where the recognition modules take them from. The boundaries matter: `point_in_area`
is open on both ends while `area_in_area` is closed, the recognition matches areas
right at the boundary of an option.
"""


def area_offset(area, offset):
    """
    Move an area.

    Args:
        area (tuple): (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
        offset (tuple): (x, y)

    Returns:
        tuple: The moved area
    """
    upper_left_x, upper_left_y, bottom_right_x, bottom_right_y = area
    x, y = offset
    return upper_left_x + x, upper_left_y + y, bottom_right_x + x, bottom_right_y + y


def point_in_area(point, area, threshold=5):
    """
    Check if a point is inside an area, the ends of the area are excluded.

    Args:
        point (tuple): (x, y)
        area (tuple): (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
        threshold (int): Allow the point to be outside the area

    Returns:
        bool:
    """
    return area[0] - threshold < point[0] < area[2] + threshold and area[1] - threshold < point[1] < area[3] + threshold


def area_in_area(area1, area2, threshold=5):
    """
    Check if the first area is inside the second one, the ends are included.

    Args:
        area1 (tuple): The area to check, (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
        area2 (tuple): The area to check against
        threshold (int): Allow the first area to exceed the second one

    Returns:
        bool:
    """
    return area2[0] - threshold <= area1[0] \
        and area2[1] - threshold <= area1[1] \
        and area1[2] <= area2[2] + threshold \
        and area1[3] <= area2[3] + threshold


def area_cross_area(area1, area2, threshold=5):
    """
    Check if two areas intersect. https://www.yiiven.cn/rect-is-intersection.html

    Args:
        area1 (tuple): (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
        area2 (tuple): (upper_left_x, upper_left_y, bottom_right_x, bottom_right_y)
        threshold (int): Allow the areas to be apart

    Returns:
        bool:
    """
    xa1, ya1, xa2, ya2 = area1
    xb1, yb1, xb2, yb2 = area2
    return abs(xb2 + xb1 - xa2 - xa1) <= xa2 - xa1 + xb2 - xb1 + threshold * 2 \
        and abs(yb2 + yb1 - ya2 - ya1) <= ya2 - ya1 + yb2 - yb1 + threshold * 2
