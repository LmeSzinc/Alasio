from collections import defaultdict

from alasio.ext.algorithm.lcp import get_lcs_length


class PathLookbackLCS:
    def __init__(self):
        self.index = 0
        # key: (file_suffix, last_char_of_stem, second_to_the_last, fullpath), value: index
        self.dict_suffix: "dict[str, dict[str, dict[str, dict[str, int]]]]" = (
            defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
        )

    @staticmethod
    def get_key(path):
        """
        Args:
            path (str):

        Returns:
            tuple[str, str]: file_suffix, last_char_of_stem, second_to_the_last
        """
        if '/' in path:
            _, _, path = path.rpartition('/')
        path, dot, suffix = path.rpartition('.')
        if dot:
            suffix = f'.{suffix}'
        else:
            suffix = ''
        last = path[-1] if path else ''
        try:
            second = path[-2]
        except IndexError:
            second = ''
        return suffix, last, second

    def get_lcs(self, path, min_length=1, max_length=None):
        """
        Args:
            path (str):
            min_length (int): Minimum LCS length for a candidate.
            max_length (int | None): Maximum LCS length for a candidate.

        Returns:
            tuple[int, int]: lookback, lcs_length
                lookback is 1-based distance to the matched item, 0 if no match
        """
        suffix, last, second = self.get_key(path)
        suffix_length = len(suffix)
        best_index = -1
        best_length = 0
        path_length = len(path)
        current_index = self.index

        # match suffix + last + second
        dict_path = self.dict_suffix[suffix][last][second]
        for prev, prev_index in reversed(dict_path.items()):
            length = get_lcs_length(path, prev)
            if length < min_length:
                continue
            if max_length is not None and length > max_length:
                continue
            if length == path_length:
                return current_index - prev_index, length
            if length > best_length:
                best_index = prev_index
                best_length = length
        if best_length:
            return current_index - best_index, best_length

        # match suffix + last
        for cand_suffix, dict_suffix in self.dict_suffix.items():
            if cand_suffix != suffix:
                continue
            for cand_last, dict_last in dict_suffix.items():
                if cand_last != last:
                    continue
                length = suffix_length + len(last)
                if max_length is not None and length > max_length:
                    continue
                # pick the one with maximum index
                for dict_path in dict_last.values():
                    if not dict_path:
                        continue
                    prev_index = next(reversed(dict_path.values()))
                    if prev_index > best_index:
                        best_index = prev_index
                        best_length = length
        if best_index != -1 and best_length >= min_length:
            return current_index - best_index, best_length

        # match suffix
        best_dict_suffix = None
        for cand_suffix, dict_suffix in self.dict_suffix.items():
            length = get_lcs_length(suffix, cand_suffix)
            if max_length is not None and length > max_length:
                continue
            if length > best_length:
                best_dict_suffix = dict_suffix
                best_length = length

        if best_length:
            # pick the one with maximum index
            for cand_last, dict_last in best_dict_suffix.items():
                for dict_path in dict_last.values():
                    if not dict_path:
                        continue
                    prev_index = next(reversed(dict_path.values()))
                    if prev_index > best_index:
                        best_index = prev_index
            if best_index != -1 and best_length >= min_length:
                return current_index - best_index, best_length

        return 0, 0

    def add_path(self, path):
        """
        Args:
            path (str):
        """
        suffix, last, second = self.get_key(path)
        self.dict_suffix[suffix][last][second][path] = self.index
        self.index += 1
