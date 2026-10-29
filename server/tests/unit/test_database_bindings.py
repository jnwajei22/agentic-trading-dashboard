from app.storage.database import postgres_sql, PostgresConnection


def test_named_parameters_preserve_literals_comments_and_postgres_casts():
    source="SELECT :amount::numeric, ':literal ?', \"colon:name\", ? -- :comment ?\n/* :ignored ? */ WHERE label LIKE '50%' AND flag=:flag"
    assert postgres_sql(source)=="SELECT %(amount)s::numeric, ':literal ?', \"colon:name\", %s -- :comment ?\n/* :ignored ? */ WHERE label LIKE '50%%' AND flag=%(flag)s"


def test_connection_preserves_mapping_values_instead_of_iterating_keys():
    calls=[]
    class Cursor:
        rowcount=1
        def execute(self,sql,params=None): calls.append((sql,params))
        def __enter__(self): return self
        def __exit__(self,*_): pass
    class Raw:
        def cursor(self,**_): return Cursor()
    connection=object.__new__(PostgresConnection);connection.raw=Raw()
    malicious="value'); DROP TABLE users; --"
    connection.execute('SELECT :payload, :enabled',{'payload':malicious,'enabled':True})
    statement=next((sql,params) for sql,params in calls if sql.startswith('SELECT'))
    assert statement==('SELECT %(payload)s, %(enabled)s',{'payload':malicious,'enabled':1})
    assert malicious not in statement[0]
