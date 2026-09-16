"""Require actual DAO collection getters for every optimized lifecycle image."""
import hashlib
import json
import zipfile

PROPERTIES = ('ConflictTable', 'ReplicaFilter')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def identity_bytes(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def identity(path):
    return identity_bytes(path.read_bytes())


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def key(image):
    return image['group'], image['event'], image['role']


class GetterSweep:
    def __init__(self, run, producer, source_run, provider):
        inbox, outbox = run / 'inbox', run / 'outbox'
        names = {'script.ps1', 'replication-property-inputs-r3.zip'}
        require({p.name for p in inbox.iterdir() if p.is_file()} == names, 'getter inbox inventory')
        require((inbox / 'script.ps1').read_bytes() == producer.read_bytes(), 'getter producer bytes')
        archive_path = inbox / 'replication-property-inputs-r3.zip'
        archive_identity = identity(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            plan_bytes = archive.read('plan.json')
            plan = json.loads(plan_bytes)
            plan_identity = identity_bytes(plan_bytes)
            images = plan['images']
            require(plan['document_type'] == 'replication_property_getter_sweep_plan'
                    and plan['source_run'] == source_run and plan['image_count'] == len(images) == 170
                    and plan['properties'] == list(PROPERTIES) and plan['worker_count'] == 4,
                    'getter plan scope')
            names = ['plan.json'] + ['files/' + image['file'] for image in images]
            require(len(names) == len(set(names)) and sorted(archive.namelist()) == sorted(names),
                    'exact getter ZIP inventory')
            for image in images:
                require(identity_bytes(archive.read('files/' + image['file'])) == image['identity'],
                        'getter input image identity')
        planned = {key(image): image for image in images}
        require(len(planned) == 170, 'unique getter image links')
        self.planned = planned
        self.seen = set()
        self.observed = {}
        expected = {'workers.json', 'exit.txt', 'log.txt'}
        expected.update(f'getter-worker-{worker}{suffix}.json'
                        for worker in range(4) for suffix in ('', '-progress'))
        require({p.name for p in outbox.iterdir() if p.is_file()} == expected, 'getter outbox inventory')
        require((outbox / 'exit.txt').read_text().strip() == '0'
                and not (outbox / 'log.txt').read_text(encoding='utf-8-sig').strip(), 'getter guest success')
        master = read(outbox / 'workers.json')
        require(master['document_type'] == 'replication_property_getter_sweep_workers'
                and master['archive'] == archive_identity and master['plan'] == plan_identity,
                'getter master input linkage')
        require(sorted(worker['worker'] for worker in master['workers']) == list(range(4)),
                'four unique getter workers')
        counts = []
        for worker in master['workers']:
            number = worker['worker']
            path = outbox / f'getter-worker-{number}.json'
            require(worker['exit_code'] == 0 and worker['result'] == identity(path), 'getter worker receipt linkage')
            receipt = read(path)
            require(receipt['document_type'] == 'replication_property_getter_sweep_worker'
                    and receipt['worker'] == number and receipt['status'] == 'pass' and receipt['error'] is None
                    and receipt['archive'] == archive_identity and receipt['plan'] == plan_identity
                    and receipt['environment'] == provider, 'getter worker identity/status/provider')
            wanted = [image for image in images if image['worker'] == number]
            require([key(image) for image in receipt['images']] == [key(image) for image in wanted],
                    'complete ordered worker image links')
            progress = read(outbox / f'getter-worker-{number}-progress.json')
            require(progress == {'worker': number, 'completed': len(wanted), 'expected': len(wanted),
                                 'last_file': wanted[-1]['file'], 'failed': False}, 'final getter worker progress')
            count = 0
            for image, spec in zip(receipt['images'], wanted):
                require(image['file'] == spec['file'] and image['error'] is None
                        and image['expected_identity'] == image['before'] == image['after'] == spec['identity'],
                        'getter image identity/preservation')
                require([{'name': table['name'], 'ordinal': table['ordinal']} for table in image['tables']]
                        == spec['tables'], 'getter table inventory')
                require(key(image) not in self.observed, 'unique getter result')
                self.observed[key(image)] = image
                count += len(image['tables'])
            require(count == plan['worker_table_loads'][number], 'getter worker table load')
            counts.append(count)
        require(set(self.observed) == set(planned), 'complete actual getter observations')
        self.summary = {'run_id': run.name, 'source_run': source_run, 'images': len(images),
                        'table_images': sum(counts), 'property_values': sum(counts) * len(PROPERTIES),
                        'archive': archive_identity, 'plan': plan_identity, 'producer': identity(producer),
                        'workers': identity(outbox / 'workers.json'),
                        'accessor': 'TableDef.Properties.Item(collection ordinal).Value'}

    def check(self, group, event, role, capture):
        wanted = (group, event, role)
        require(wanted not in self.seen and wanted in self.observed, 'one getter comparison per image')
        self.seen.add(wanted)
        image = self.observed[wanted]
        require(image['before'] == capture['before'] == capture['after'], 'getter/lifecycle same closed image')
        tables = capture['snapshot']['tables']
        require([{'name': table['name']['value'], 'ordinal': table['ordinal']} for table in tables]
                == self.planned[wanted]['tables'], 'getter/lifecycle complete table linkage')
        for actual, table in zip(image['tables'], tables):
            expected = [prop for prop in table['properties'] if prop['name']['value'] in PROPERTIES]
            require([prop['name'] for prop in actual['properties']] == list(PROPERTIES)
                    and [prop['name']['value'] for prop in expected] == list(PROPERTIES),
                    'two complete replication property observations')
            for observed, saved in zip(actual['properties'], expected):
                require(observed['error'] is None and isinstance(observed['elapsed_ms'], int)
                        and observed['elapsed_ms'] >= 0, 'actual native getter succeeded')
                require(all(type(observed[name]) is bool for name in
                            ('is_null', 'is_ps_null', 'is_dbnull', 'is_empty_string')), 'explicit native value classes')
                require(observed['is_null'] == (observed['is_ps_null'] or observed['is_dbnull'])
                        and observed['is_empty_string'] == (not observed['is_null'] and observed['value'] == '')
                        and (observed['value'] is None) == observed['is_null'], 'native value-class consistency')
                require(all(observed[name] == saved[name] for name in ('ordinal', 'type', 'is_null', 'value')),
                        f'actual collection getter equals complete snapshot: {wanted}/{actual["name"]}/{observed["name"]}')

    def finish(self):
        require(self.seen == set(self.planned), 'all lifecycle images have actual getter comparisons')
        return self.summary
