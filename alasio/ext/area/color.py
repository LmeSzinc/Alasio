class RGB(tuple):
    def as_int(self, round_value=True) -> "RGB":
        """
        Ensure value are integer
        """
        r, g, b = self
        if round_value:
            return RGB((int(round(r)), int(round(g)), int(round(b))))
        else:
            return RGB((int(r), int(g), int(b)))
