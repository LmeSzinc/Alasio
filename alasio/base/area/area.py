class Point(tuple):
    @property
    def x(self):
        return self[0]

    @property
    def y(self):
        return self[1]

    def move(self, vector: "Point") -> "Point":
        """
        Move point
        """
        return Point((self[0] + vector[0], self[1] + vector[1]))

    def move_x(self, value) -> "Point":
        """
        Move point along X axis
        """
        return Point((self[0] + value, self[1]))

    def move_y(self, value) -> "Point":
        """
        Move point along Y axis
        """
        return Point((self[0], self[1] + value))

    def is_in_screen(self, res: "tuple[int, int]") -> bool:
        """
        Check if the point is within (0, 0, resolution_x, resolution_y)
        """
        return 0 <= self[0] < res[0] and 0 <= self[1] < res[1]

    def is_in_area(self, area: "Area") -> bool:
        """
        Check if the point is within the area
        """
        return area[0] <= self[0] <= area[2] and area[1] <= self[1] <= area[3]

    def limit_in_screen(self, res: "tuple[int, int]") -> "Point":
        """
        Limit area within (0, 0, resolution_x, resolution_y)

        Returns:
            Area: If point is within screen, return self
                otherwise, return new point within screen
        """
        if self[0] >= 0:
            if self[0] < res[0]:
                if self[1] >= 0:
                    if self[1] < res[1]:
                        return self
                    else:
                        return Point((self[0], res[1] - 1))
                else:
                    return Point((self[0], 0))
            else:
                if self[1] >= 0:
                    if self[1] < res[1]:
                        return Point((res[0] - 1, self[1]))
                    else:
                        return Point((res[0] - 1, res[1] - 1))
                else:
                    return Point((res[0] - 1, 0))
        else:
            if self[1] >= 0:
                if self[1] < res[1]:
                    return Point((0, self[1]))
                else:
                    return Point((0, res[1] - 1))
            else:
                return Point((0, 0))

    def limit_in_area(self, area: "Area") -> "Point":
        """
        Limit point within area

        Returns:
            Area: If area is already within, return self
                otherwise, return new point within given area
        """
        if self[0] >= area[0]:
            if self[0] <= area[2]:
                if self[1] >= area[1]:
                    if self[1] <= area[3]:
                        return self
                    else:
                        return Point((self[0], area[3]))
                else:
                    return Point((self[0], area[1]))
            else:
                if self[1] >= area[1]:
                    if self[1] <= area[3]:
                        return Point((area[2], self[1]))
                    else:
                        return Point((area[2], area[3]))
                else:
                    return Point((area[2], area[1]))
        else:
            if self[1] >= area[1]:
                if self[1] <= area[3]:
                    return Point((area[0], self[1]))
                else:
                    return Point((area[0], area[3]))
            else:
                return Point((area[0], area[1]))


class Area(tuple):
    @classmethod
    def from_xywh(self, xywh: "tuple[int, int, int, int]") -> "Area":
        """
        Convert a xywh area to xyxy area
        """
        return Area((xywh[0], xywh[1], xywh[0] + xywh[2], xywh[1], xywh[3]))

    @property
    def upperleft(self) -> "Point":
        return Point((self[0], self[1]))

    @property
    def upperright(self) -> "Point":
        return Point((self[2], self[1]))

    @property
    def bottomleft(self) -> "Point":
        return Point((self[0], self[3]))

    @property
    def bottomright(self) -> "Point":
        return Point((self[2], self[3]))

    def is_valid(self) -> bool:
        """
        Area should be at least 1x1, otherwise invalid
        """
        return self[2] > self[0] and self[3] > self[1]

    def is_in_screen(self, res: "tuple[int, int]") -> bool:
        """
        Check if area is within (0, 0, resolution_x, resolution_y)
        """
        return 0 <= self[0] and 0 <= self[1] and self[2] <= res[0] and self[3] <= res[1]

    def is_in_area(self, area: "Area") -> bool:
        """
        Check if area is within another area
        """
        return area[0] <= self[0] and area[1] <= self[1] and self[2] <= area[2] and self[3] <= area[3]

    def limit_in_screen(self, res: "tuple[int, int]") -> "Area":
        """
        Limit area within (0, 0, resolution_x, resolution_y)

        Returns:
            Area: If area is valid, return self
                otherwise, return new area within screen
        """
        if self[0] >= 0:
            if self[1] >= 0:
                if self[2] <= res[0]:
                    if self[3] <= res[1]:
                        return self
                    else:
                        return Area((self[0], self[1], self[2], res[1]))
                else:
                    if self[3] <= res[1]:
                        return Area((self[0], self[1], res[0], self[3]))
                    else:
                        return Area((self[0], self[1], res[0], res[1]))
            else:
                if self[2] <= res[0]:
                    if self[3] <= res[1]:
                        return Area((self[0], 0, self[2], self[3]))
                    else:
                        return Area((self[0], 0, self[2], res[1]))
                else:
                    if self[3] <= res[1]:
                        return Area((self[0], 0, res[0], self[3]))
                    else:
                        return Area((self[0], 0, res[0], res[1]))
        else:
            if self[1] >= 0:
                if self[2] <= res[0]:
                    if self[3] <= res[1]:
                        return Area((0, self[1], self[2], self[3]))
                    else:
                        return Area((0, self[1], self[2], res[1]))
                else:
                    if self[3] <= res[1]:
                        return Area((0, self[1], res[0], self[3]))
                    else:
                        return Area((0, self[1], res[0], res[1]))
            else:
                if self[2] <= res[0]:
                    if self[3] <= res[1]:
                        return Area((0, 0, self[2], self[3]))
                    else:
                        return Area((0, 0, self[2], res[1]))
                else:
                    if self[3] <= res[1]:
                        return Area((0, 0, res[0], self[3]))
                    else:
                        return Area((0, 0, res[0], res[1]))

    def limit_in_area(self, area: "Area") -> "Area":
        """
        Limit area within another area

        Returns:
            Area: If area is already within, return self
                otherwise, return new area within given area
        """
        if self[0] >= area[0]:
            if self[1] >= area[1]:
                if self[2] <= area[2]:
                    if self[3] <= area[3]:
                        return self
                    else:
                        return Area((self[0], self[1], self[2], area[3]))
                else:
                    if self[3] <= area[3]:
                        return Area((self[0], self[1], area[2], self[3]))
                    else:
                        return Area((self[0], self[1], area[2], area[3]))
            else:
                if self[2] <= area[2]:
                    if self[3] <= area[3]:
                        return Area((self[0], area[1], self[2], self[3]))
                    else:
                        return Area((self[0], area[1], self[2], area[3]))
                else:
                    if self[3] <= area[3]:
                        return Area((self[0], area[1], area[2], self[3]))
                    else:
                        return Area((self[0], area[1], area[2], area[3]))
        else:
            if self[1] >= area[1]:
                if self[2] <= area[2]:
                    if self[3] <= area[3]:
                        return Area((area[0], self[1], self[2], self[3]))
                    else:
                        return Area((area[0], self[1], self[2], area[3]))
                else:
                    if self[3] <= area[3]:
                        return Area((area[0], self[1], area[2], self[3]))
                    else:
                        return Area((area[0], self[1], area[2], area[3]))
            else:
                if self[2] <= area[2]:
                    if self[3] <= area[3]:
                        return Area((area[0], area[1], self[2], self[3]))
                    else:
                        return Area((area[0], area[1], self[2], area[3]))
                else:
                    if self[3] <= area[3]:
                        return Area((area[0], area[1], area[2], self[3]))
                    else:
                        return Area((area[0], area[1], area[2], area[3]))

    def is_intersect_area(self, area: "Area") -> bool:
        """
        Check if this area intersects with another area
        https://www.yiiven.cn/rect-is-intersection.html
        """
        diff_x = area[2] + area[0] - self[2] - self[0]
        if diff_x < 0:
            diff_x = -diff_x
        diff_y = area[3] + area[1] - self[3] - self[1]
        if diff_y < 0:
            diff_y = -diff_y
        return diff_x <= self[2] - self[0] + area[2] - area[0] and diff_y <= self[3] - self[1] + area[3] - area[1]

    def move(self, vector: "Point") -> "Area":
        """
        Moves the area by the given vector
        """
        return Area((self[0] + vector[0], self[1] + vector[1], self[2] + vector[0], self[3] + vector[1]))

    def move_x(self, value) -> "Area":
        """
        Move area along X axis
        """
        return Area((self[0] + value, self[1], self[2] + value, self[3]))

    def move_y(self, value) -> "Area":
        """
        Move area along Y axis
        """
        return Area((self[0], self[1] + value, self[2], self[3] + value))

    def move_onto_upperleft(self, area) -> "Area":
        """
        Move area onto the upperleft of another area
        Equivalent to:
            self.move(area.upperleft)
        """
        return Area((self[0] + area[0], self[1] + area[1], self[2] + area[0], self[3] + area[1]))

    def pad(self, pad: int) -> "Area":
        """
        Shrinks the area by padding amount on all sides
        """
        return Area((self[0] + pad, self[1] + pad, self[2] - pad, self[3] - pad))

    def size(self) -> "Point":
        """
        Gets the width and height of the area
        """
        return Point((self[2] - self[0], self[3] - self[1]))

    def center(self) -> "Point":
        """
        Gets the center point of the area
        """
        return Point(((self[0] + self[2]) // 2, (self[1] + self[3]) // 2))
