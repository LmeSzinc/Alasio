from collections import defaultdict

from alasio.ext.algorithm.lcp import get_lcs_length


class PathLookbackLCS:
    def __init__(self):
        self.index = 0
        # key: (file_suffix, last_char_of_stem, fullpath), value: index
        self.dict_suffix: "dict[str, dict[str, dict[str, int]]]" = defaultdict(lambda: defaultdict(dict))

    @staticmethod
    def get_key(path):
        """
        Args:
            path (str):

        Returns:
            tuple[str, str]: file_suffix, last_char_of_stem
        """
        path, dot, suffix = path.rpartition('.')
        if dot:
            suffix = f'.{suffix}'
        else:
            suffix = ''
        char = path[-1] if path else ''
        return suffix, char

    def get_lcs(self, path, min_length=1, max_length=None, max_lookback=None):
        """
        Args:
            path (str):
            min_length (int): Minimum LCS length for a candidate.
            max_length (int | None): Maximum LCS length for a candidate.
            max_lookback (int | None): Maximum lookback distance.

        Returns:
            tuple[int, int]: lookback, lcs_length
                lookback is 1-based distance to the matched item, 0 if no match)
        """
        suffix, char = self.get_key(path)
        best_index = 0
        best_length = 0
        path_length = len(path)
        current_index = self.index

        # match suffix and char
        dict_path = self.dict_suffix[suffix][char]
        for prev, prev_index in reversed(list(dict_path.items())):
            length = get_lcs_length(path, prev)
            if length < min_length:
                continue
            if max_length is not None and length > max_length:
                continue
            if max_lookback is not None and current_index - prev_index > max_lookback:
                continue
            if length == path_length:
                return current_index - prev_index, length
            if length > best_length:
                best_index = prev_index
                best_length = length
        if best_length:
            return current_index - best_index, best_length

        # match suffix
        for dict_path in self.dict_suffix[suffix].values():
            for prev, prev_index in reversed(list(dict_path.items())):
                length = get_lcs_length(path, prev)
                if length < min_length:
                    continue
                if max_length is not None and length > max_length:
                    continue
                if max_lookback is not None and current_index - prev_index > max_lookback:
                    continue
                if length == path_length:
                    return current_index - prev_index, length
                if length > best_length:
                    best_index = prev_index
                    best_length = length
        if best_length:
            return current_index - best_index, best_length

        # match any
        for dict_suffix in self.dict_suffix.values():
            for dict_path in dict_suffix.values():
                for prev, prev_index in reversed(list(dict_path.items())):
                    length = get_lcs_length(path, prev)
                    if length < min_length:
                        continue
                    if max_length is not None and length > max_length:
                        continue
                    if max_lookback is not None and current_index - prev_index > max_lookback:
                        continue
                    if length == path_length:
                        return current_index - prev_index, length
                    if length > best_length:
                        best_index = prev_index
                        best_length = length

        if best_length:
            return current_index - best_index, best_length
        return 0, 0

    def add_path(self, path):
        """
        Args:
            path (str):
        """
        suffix, char = self.get_key(path)
        self.dict_suffix[suffix][char][path] = self.index
        self.index += 1
