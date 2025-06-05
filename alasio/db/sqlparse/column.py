from alasio.db.sqlparse.utils import first_paren_content, first_token


def get_primary_key(sql):
    """
    Get the column name if this sql row is primary key, quotes will be preserved.
    Note that in composite primary key, all columns will be returned: "OrderID, ProductID"

    Args:
        sql (str): A row of sql in CREATE TABLE

    Returns:
        str:
    """
    sql_upper = sql.upper()
    # find "PRIMARY"
    before, sep, after = sql_upper.partition('PRIMARY')
    if not sep:
        return ''
    # "ID INT MYPRIMARY KEY (ID)"
    if before and before[-1] not in ' \t\r\n':
        return ''
    # find "KEY"
    middle, sep, after = after.partition('KEY')
    if not sep:
        return ''
    # between "PRIMARY" and "KEY", there should only be spaces
    if middle.strip():
        return ''

    index = len(before) + len(middle) + 10
    after = sql[index:]

    # find first paren
    # Out-of-Line Definition: "PRIMARY KEY CLUSTERED (id)"
    # Composite Primary Key: "PRIMARY KEY (OrderID, ProductID)"
    pk = first_paren_content(after).strip()
    if pk:
        return pk

    # otherwise, this sql might be a column
    # its first token is probably the column name
    # Inline Definition: "EmployeeID INT PRIMARY KEY,"
    pk = first_token(sql)

    # check if first token is sql keyword
    if pk.upper() in ['CONSTRAINT', 'PRIMARY', 'UNIQUE', 'FOREIGN', 'CHECK', 'TABLE']:
        return ''
    return pk
