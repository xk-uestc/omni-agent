"""Fetch pinned Microsoft original CSV; explicitly audited SQLite subset port."""
from __future__ import annotations
import argparse
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
COMMIT='1346b94fab23bfa7edfdb32afae52282ddc3bfaa'
BASE=f'https://raw.githubusercontent.com/microsoft/sql-server-samples/{COMMIT}/'
PREFIX='samples/databases/adventure-works/oltp-install-script/'
TABLES=('SalesTerritory','Customer','SalesOrderHeader')
DEFAULT=Path('D:/ICT8-OfficialDatasets/adventureworks')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def table_contract(sql, table):
    body=re.search(r'CREATE TABLE \[Sales\]\.\['+re.escape(table)+r'\]\s*\(([\s\S]+?)\) ON \[PRIMARY\];',sql).group(1)
    columns=[]
    for line in body.splitlines():
        match=re.match(r'\s*\[([^]]+)\]\s+(.*)',line)
        if not match:
            continue
        name,definition=match.groups()
        if definition.startswith('AS '):
            dtype='NUMERIC' if name=='TotalDue' else 'TEXT'
            kind='computed_original_csv_value'
        else:
            original=re.match(r'(?:\[([^]]+)\]|(uniqueidentifier|nvarchar))',definition)
            if not original:
                raise ValueError('Unknown official field type: '+line)
            source_type=(original.group(1) or original.group(2)).lower()
            dtype='INTEGER' if source_type in {'int','tinyint','smallint','bit','flag'} else 'NUMERIC' if source_type in {'money','decimal','numeric'} else 'DATETIME' if source_type=='datetime' else 'TEXT'
            kind=source_type
        columns.append({'name':name,'sqlite_type':dtype,'source_type':kind,'nullable':' NOT NULL' not in definition and not definition.startswith('AS ')})
    primary=re.search(r'CONSTRAINT \[PK_'+table+r'_[^]]+\] PRIMARY KEY CLUSTERED\s*\(([\s\S]+?)\)',sql)
    pks=re.findall(r'\[([^]]+)\]',primary.group(1))
    fks=[]
    for block in re.finditer(r'CONSTRAINT \[FK_'+table+r'_[^]]+\] FOREIGN KEY\s*\(([^)]+)\) REFERENCES \[([^]]+)\]\.\[([^]]+)\]\s*\(([^)]+)\)',sql):
        own,schema,target,foreign=block.groups()
        fks.append({'columns':re.findall(r'\[([^]]+)\]',own),'schema':schema,'table':target,'target_columns':re.findall(r'\[([^]]+)\]',foreign)})
    bulk=re.search(r'BULK INSERT \[Sales\]\.\['+table+r'\][\s\S]+?\);',sql).group()
    if "CODEPAGE = '65001'" not in bulk or "FIELDTERMINATOR = '\\t'" not in bulk:
        raise ValueError('Unexpected official import encoding or delimiter')
    return {'columns':columns,'primary_key':pks,'internal_foreign_keys':[f for f in fks if f['schema']=='Sales' and f['table'] in TABLES],
            'omitted_external_foreign_keys':[f for f in fks if not(f['schema']=='Sales' and f['table'] in TABLES)]}


def convert_cell(value, field):
    if not value:
        if not field['nullable']:
            raise ValueError('NULL in NOT NULL '+field['name'])
        return None
    if field['sqlite_type']=='INTEGER':
        return int(value)
    if field['sqlite_type']=='NUMERIC':
        decimal=Decimal(value)
        if not decimal.is_finite() or decimal.as_tuple().exponent < -4:
            raise ValueError('Unexpected money precision')
        return str(decimal)
    if field['sqlite_type']=='DATETIME':
        datetime.fromisoformat(value)
    return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,default=DEFAULT)
    args=parser.parse_args()
    target=args.directory.resolve()
    target.mkdir(parents=True,exist_ok=True)
    raw=target/'original';raw.mkdir(exist_ok=True)
    manifest_path=target/'MANIFEST.json'
    if manifest_path.exists():
        raise SystemExit('Existing frozen dataset manifest; do not overwrite')
    sources=[]
    for file,relative in [('license.txt','license.txt'),('instawdb.sql',PREFIX+'instawdb.sql'),*((t+'.csv',PREFIX+t+'.csv') for t in TABLES)]:
        destination=raw/file
        if destination.exists():
            raise SystemExit('Unexpected pre-existing raw asset: '+str(destination))
        url=BASE+relative
        with urllib.request.urlopen(url,timeout=120) as response:
            destination.write_bytes(response.read())
        sources.append({'file':'original/'+file,'url':url,'bytes':destination.stat().st_size,'sha256':digest(destination)})
        print(json.dumps({'downloaded':file,'bytes':destination.stat().st_size}),flush=True)
    license=(raw/'license.txt').read_text(encoding='utf-8-sig')
    if 'MIT License' not in license:
        raise ValueError('Official license requires review')
    sql=(raw/'instawdb.sql').read_text(encoding='utf-8-sig')
    contracts={table:table_contract(sql,table) for table in TABLES}
    database=target/'adventureworks-subset.sqlite'
    if database.exists():
        raise SystemExit('Database exists; do not overwrite')
    statistics={}
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        for table,contract in contracts.items():
            definitions=[f'"{f["name"]}" {f["sqlite_type"]}'+(' NOT NULL' if not f['nullable'] else '') for f in contract['columns']]
            definitions.append('PRIMARY KEY('+','.join('"'+k+'"' for k in contract['primary_key'])+')')
            for fk in contract['internal_foreign_keys']:
                definitions.append('FOREIGN KEY('+','.join('"'+k+'"' for k in fk['columns'])+') REFERENCES "'+fk['table']+'"('+','.join('"'+k+'"' for k in fk['target_columns'])+')')
            connection.execute('CREATE TABLE "'+table+'"('+','.join(definitions)+')')
            fields=contract['columns']
            count=0
            exact_sums={f['name']:Decimal(0) for f in fields if f['sqlite_type']=='NUMERIC'}
            with (raw/(table+'.csv')).open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.reader(stream,delimiter='\t',quoting=csv.QUOTE_NONE):
                    if len(row)!=len(fields):
                        raise ValueError(f'{table} field width mismatch at {count+1}: {len(row)} != {len(fields)}')
                    values=[convert_cell(value,field) for value,field in zip(row,fields)]
                    mapping=dict(zip((f['name'] for f in fields),values))
                    if table=='Customer' and mapping['AccountNumber']!=f"AW{mapping['CustomerID']:08d}":
                        raise ValueError('Customer computed AccountNumber mismatch')
                    if table=='SalesOrderHeader':
                        if mapping['SalesOrderNumber']!='SO'+str(mapping['SalesOrderID']):
                            raise ValueError('SalesOrderNumber mismatch')
                        if Decimal(mapping['TotalDue'])!=sum((Decimal(mapping[k]) for k in ('SubTotal','TaxAmt','Freight')),Decimal(0)):
                            raise ValueError('TotalDue computed expression mismatch')
                    for name in exact_sums:
                        if mapping[name] is not None:
                            exact_sums[name]+=Decimal(mapping[name])
                    connection.execute('INSERT INTO "'+table+'" VALUES('+','.join('?' for _ in fields)+')',values)
                    count+=1
            statistics[table]={'rows':count,'fields':len(fields),'raw_decimal_sums':{k:str(v) for k,v in exact_sums.items()}}
        violations=connection.execute('PRAGMA foreign_key_check').fetchall()
        integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
        if violations or integrity!='ok':
            raise ValueError('SQLite migration integrity failed')
    manifest={'source_repository':'https://github.com/microsoft/sql-server-samples','source_commit':COMMIT,'license':'MIT',
        'scope':'official_original_csv_sqlite_subset_port_not_native_adventureworks_postgres_or_official_question_score',
        'assets':sources,'table_contracts':contracts,'statistics':statistics,'database':{'file':database.name,'bytes':database.stat().st_size,'sha256':digest(database)},
        'limitations':['SQL Server schema Sales flattened to bare SQLite names; selected tables only.',
            'SQL Server money converted from validated four-decimal Decimal strings to SQLite NUMERIC affinity (binary floating storage); raw exact sums retained.',
            'Datetime strings kept and validated, uniqueidentifier stored as TEXT; SQL Server identity/defaults/triggers/non-PK indexes not reproduced.',
            'Computed columns retained from original CSV and checked against official expressions; internal FKs retained, external FKs explicitly omitted.',
            'No selected table has a composite primary key; no claim of testing composite key compatibility.',
            'No native PostgreSQL adapter, XML/geography/custom-type/functions/procedures support or official benchmark score is claimed.']}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'ready','manifest':str(manifest_path),'statistics':statistics},ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
