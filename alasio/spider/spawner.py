class PrioritySpawner:
    def __init__(self, parent, **kwargs):
        """
        Priority spawner to manage task priorities in a hierarchy.

        Args:
            parent (PrioritySpawner | None): Parent spawner.
                Must be a PrioritySpawner instance.
            **kwargs: Arbitrary keyword arguments for cooperative multiple inheritance.
        """
        if parent is not None:
            if not isinstance(parent, PrioritySpawner):
                raise ValueError('PrioritySpawner parent must be another PrioritySpawner')
            self._spawner_priority = parent.new_child()
        else:
            self._spawner_priority = tuple()

        self._spawner_child_index = 0
        super().__init__(**kwargs)

    def new_child(self):
        """
        Create a new child priority tuple.

        Returns:
            tuple[int, ...]: New priority tuple for the child.
        """
        self._spawner_child_index += 1
        if self._spawner_priority:
            return *self._spawner_priority, self._spawner_child_index
        else:
            return (self._spawner_child_index,)

    @property
    def heapq_priority(self):
        """
        Calculate priority for heapq (min-heap).

        Children will have higher heapq_priority compares to parents, meaning to run parent first.
        Parent's brother will have lower heapq_priority compares to children,
        meaning parent's brothers will cut the line and delay waiting children.
        Logic: parent < parent's brothers < child < child's brothers

        Returns:
            tuple[int, ...]: Priority tuple for heapq.
        """
        priority = self._spawner_priority
        priority = (-len(priority), *priority)
        return priority


class RootSpawner(PrioritySpawner):
    def __init__(self, parent=None, **kwargs):
        """
        Root spawner that has no parent.

        Args:
            parent: Ignored.
            **kwargs: Arbitrary keyword arguments.
        """
        super().__init__(parent=None, **kwargs)
