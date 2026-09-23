function Base($db) {
  Make-Table $db 'P' @('id:long','x:long','m:memo','o:ole','t:text:10') @('PrimaryKey:primary:id')
  Make-Table $db 'C' @('id:long','pid:long','m:memo','o:ole','t:text:10','qid:long') @('PrimaryKey:primary:id')
  Make-Table $db 'Q' @('id:long','n:text:10') @('PrimaryKey:primary:id')
}
function Seed($db) {
  Sql $db 'INSERT INTO P (id, x, t) VALUES (1, 10, ''a'')'; Sql $db 'INSERT INTO P (id, x, t) VALUES (2, 10, ''b'')'
  Sql $db 'INSERT INTO Q (id, n) VALUES (1, ''q'')'
  Sql $db 'INSERT INTO C (id, pid, qid, t) VALUES (1, 1, 1, ''a'')'
}
$Cases = @()
foreach ($a in @(0,1,2,3,4,8,16,256,4096,4352,257,4097,258,4098,4354,16777216,33554432,50331648,16781568,33558784,16777218,33554434,16777472,33558528,65536,512)) {
  $Cases += @{ name = "attr-$a"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'P' 'C' $a @('id=pid')")) }
}
$Cases += @{ name = 'attr-1-uniquechild'; replicas = 2; tables = @(); steps = @({ param($db) Base $db; Sql $db 'CREATE UNIQUE INDEX ux ON C (pid)' }, { param($db) Relate $db 'R' 'P' 'C' 1 @('id=pid') }) }
$Cases += @{ name = 'attr-0-uniquechild'; replicas = 2; tables = @(); steps = @({ param($db) Base $db; Sql $db 'CREATE UNIQUE INDEX ux ON C (pid)' }, { param($db) Relate $db 'R' 'P' 'C' 0 @('id=pid') }) }
$Cases += @{ name = 'attr-2-plainchild'; replicas = 2; tables = @(); steps = @({ param($db) Base $db; Sql $db 'CREATE INDEX ix ON C (pid)' }, { param($db) Relate $db 'R' 'P' 'C' 2 @('id=pid') }) }
$Cases += @{ name = 'attr-2-uniquechild'; replicas = 2; tables = @(); steps = @({ param($db) Base $db; Sql $db 'CREATE UNIQUE INDEX ux ON C (pid)' }, { param($db) Relate $db 'R' 'P' 'C' 2 @('id=pid') }) }
foreach ($a in @(0, 2)) {
  $Cases += @{ name = "form-$a-nonunique"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'P' 'C' $a @('x=pid')")) }
  $Cases += @{ name = "form-$a-memo"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'P' 'C' $a @('m=m')")) }
  $Cases += @{ name = "form-$a-ole"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'P' 'C' $a @('o=o')")) }
  $Cases += @{ name = "form-$a-mismatch"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'P' 'C' $a @('id=t')")) }
  $Cases += @{ name = "form-$a-composite"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'P' 'C' $a @('id=pid','x=qid')")) }
  $Cases += @{ name = "form-$a-self"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'C' 'C' $a @('id=pid')")) }
  $Cases += @{ name = "form-$a-memo-nonindexed-parent"; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, [scriptblock]::Create("param(`$db) Relate `$db 'R' 'P' 'C' $a @('t=t')")) }
}
$Cases += @{ name = 'form-2-orphans-existing'; replicas = 2; tables = @('P','C'); steps = @({ param($db) Base $db; Seed $db; Sql $db 'INSERT INTO C (id, pid) VALUES (9, 99)' }, { param($db) Relate $db 'R' 'P' 'C' 2 @('id=pid') }, { param($db) Relate $db 'E' 'P' 'C' 0 @('id=pid') }) }
$Cases += @{ name = 'form-duplicate-pair'; replicas = 2; tables = @(); steps = @({ param($db) Base $db }, { param($db) Relate $db 'R' 'P' 'C' 2 @('id=pid') }, { param($db) Relate $db 'S' 'P' 'C' 2 @('id=pid') }, { param($db) Relate $db 'E' 'P' 'C' 0 @('id=pid') }) }
# Lifecycles: build, checkpoint (input image), then operate.
function Life($name, $attr, $ops) {
  $setup = [scriptblock]::Create("param(`$db) Base `$db; Seed `$db; Relate `$db 'R' 'P' 'C' $attr @('id=pid')")
  return @{ name = $name; replicas = 2; tables = @('P','C','Q'); steps = @($setup, 'checkpoint') + $ops }
}
foreach ($a in @(2, 0, 16777218)) {
  $Cases += Life "life-$a-writes" $a @(
    { param($db) Sql $db 'INSERT INTO C (id, pid) VALUES (2, 99)' },
    { param($db) Sql $db 'UPDATE C SET pid = 77 WHERE id = 1' },
    { param($db) Sql $db 'INSERT INTO C (id, pid) VALUES (3, 2)' },
    { param($db) Sql $db 'UPDATE P SET id = 5 WHERE id = 2' },
    { param($db) Sql $db 'DELETE FROM P WHERE id = 5' },
    { param($db) Sql $db 'DELETE FROM P WHERE id = 1' })
  $Cases += Life "life-$a-drop-child" $a @({ param($db) DropTable $db 'C' })
  $Cases += Life "life-$a-drop-parent" $a @({ param($db) DropTable $db 'P' })
  $Cases += Life "life-$a-drop-child-col" $a @({ param($db) DropColumn $db 'C' 'pid' })
  $Cases += Life "life-$a-drop-parent-col" $a @({ param($db) DropColumn $db 'P' 'id' })
  $Cases += Life "life-$a-drop-other-col" $a @({ param($db) DropColumn $db 'C' 't' })
  $Cases += Life "life-$a-rename-child" $a @({ param($db) RenameTable $db 'C' 'C2' })
  $Cases += Life "life-$a-rename-parent" $a @({ param($db) RenameTable $db 'P' 'P2' })
  $Cases += Life "life-$a-rename-child-col" $a @({ param($db) RenameColumn $db 'C' 'pid' 'pid2' })
  $Cases += Life "life-$a-rename-parent-col" $a @({ param($db) RenameColumn $db 'P' 'id' 'id2' })
  $Cases += Life "life-$a-drop-relation" $a @({ param($db) Unrelate $db 'R' })
  $Cases += Life "life-$a-rename-relation" $a @({ param($db) RenameRelation $db 'R' 'R2' })
  $Cases += Life "life-$a-set-attributes" $a @({ param($db) SetRelationAttributes $db 'R' 0 })
  $Cases += Life "life-$a-drop-parent-pk" $a @({ param($db) Sql $db 'DROP INDEX PrimaryKey ON P' })
}
$Cases += @{ name = 'life-mixed'; replicas = 2; tables = @('P','C','Q'); steps = @(
  { param($db) Base $db; Seed $db; Relate $db 'E' 'P' 'C' 0 @('id=pid'); Relate $db 'U' 'Q' 'C' 2 @('id=qid') }, 'checkpoint',
  { param($db) Sql $db 'INSERT INTO C (id, pid, qid) VALUES (2, 1, 99)' },
  { param($db) Sql $db 'INSERT INTO C (id, pid, qid) VALUES (3, 98, 1)' },
  { param($db) Sql $db 'UPDATE C SET qid = 50 WHERE id = 1' },
  { param($db) Sql $db 'DELETE FROM Q WHERE id = 1' },
  { param($db) Sql $db 'DELETE FROM P WHERE id = 1' },
  { param($db) Sql $db 'DELETE FROM P WHERE id = 2' }) }
$Cases += @{ name = 'life-2-nonunique-writes'; replicas = 2; tables = @('P','C'); steps = @(
  { param($db) Base $db; Seed $db; Relate $db 'R' 'P' 'C' 2 @('x=pid') }, 'checkpoint',
  { param($db) Sql $db 'INSERT INTO C (id, pid) VALUES (2, 10)' },
  { param($db) Sql $db 'DELETE FROM P' },
  { param($db) DropTable $db 'P' }) }
