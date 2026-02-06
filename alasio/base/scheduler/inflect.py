class Inflection:
    """
    A utility class to convert strings between different naming conventions
    (kebab-case, PascalCase, snake_case).
    """

    def __init__(self, words: "list[str]"):
        """
        Initialize with a list of clean words.
        Note: Use the class method `from_string` to instantiate this class easily.
        """
        self.words = words

    @classmethod
    def from_string(cls, text: str):
        """
        Class method to load a string, parse it into words, and return an instance.

        It handles:
        - CamelCase and PascalCase splitting.
        - Removal of apostrophes (e.g., "Bob's" -> "Bobs").
        - Cleaning of special characters and extra underscores.
        """
        if not text:
            return cls([])

        import re

        # 1. Pre-processing: Remove apostrophes/single quotes.
        # Logic: "Bob's Mod" becomes "Bobs Mod" to avoid splitting into "Bob" and "s".
        text = re.sub(r"['’]", "", text)

        # 2. Handle CamelCase: Insert underscore between lower/digit and Upper.
        # Logic: "camelCase" -> "camel_Case"
        text = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', text)

        # 3. Handle acronyms followed by a word.
        # Logic: "HTTPServer" -> "HTTP_Server"
        text = re.sub(r'([A-Z])([A-Z][a-z])', r'\1_\2', text)

        # 4. Replace non-alphanumeric characters with underscores.
        # Logic: Treats spaces, hyphens, dots, etc., as separators.
        text = re.sub(r'[^a-zA-Z0-9]', '_', text)

        # 5. Split by underscore and filter out empty strings.
        # Logic: Handles "My__internal" by ignoring the empty strings between underscores.
        words = [w for w in text.split('_') if w]

        return cls(words)

    def to_kebab_case(self) -> str:
        """
        Format: one-function-name
        Logic: All lowercase, joined by hyphens.
        """
        return "-".join(word.lower() for word in self.words)

    def to_pascal_case(self) -> str:
        """
        Format: OneFunctionName
        Logic: First letter of every word uppercase, joined without separators.
        """
        return "".join(word.capitalize() for word in self.words)

    def to_snake_case(self) -> str:
        """
        Format: one_function_name
        Logic: All lowercase, joined by underscores.
        """
        return "_".join(word.lower() for word in self.words)

    def to_camel_case(self) -> str:
        """
        Format: oneFunctionName (Bonus)
        Logic: First word lowercase, subsequent words capitalized.
        """
        if not self.words:
            return ""
        # First word lowercase + remaining words capitalized
        return self.words[0].lower() + "".join(word.capitalize() for word in self.words[1:])
