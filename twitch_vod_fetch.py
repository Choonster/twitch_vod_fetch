#!/usr/bin/env python2
# -*- coding: utf-8 -*-
from __future__ import print_function

import itertools as it, operator as op, functools as ft
from collections import OrderedDict
from contextlib import contextmanager, closing
from os.path import exists, dirname, isdir
import subprocess, tempfile, time, glob, socket
import os, sys, re, json, types, base64
import shutil

import requests

mswindows = (sys.platform == "win32")

it_adjacent = lambda seq, n: it.izip_longest(*([iter(seq)] * n))

def get_uid(n=3):
	assert n * 8 % 6 == 0, n
	return base64.urlsafe_b64encode(os.urandom(n))

def log_lines(log_func, lines, log_func_last=False):
	'''Log passed sequence of lines or a newline-delimited string via log_func.
		Sequence elements can also be tuples of (template, *args, **kws) to pass to log_func.
		log_func_last (if passed) will be applied to the last line instead of log_func,
			with idea behind it is to pass e.g. log.exception there,
			so it'd dump proper traceback at the end of the message.'''
	if isinstance(lines, types.StringTypes):
		lines = list(line.rstrip() for line in lines.rstrip().split('\n'))
	uid = get_uid()
	for n, line in enumerate(lines, 1):
		if isinstance(line, types.StringTypes): line = '[%s] %s', uid, line
		else: line = ('[{}] {}'.format(uid, line[0]),) + line[1:]
		if log_func_last and n == len(lines): log_func_last(*line)
		else: log_func(*line)

def parse_pos_spec(pos):
	try: mins, secs = pos.rsplit(':', 1)
	except ValueError: hrs, mins, secs = 0, 0, pos
	else:
		try: hrs, mins = mins.rsplit(':', 1)
		except ValueError: hrs = 0
	return sum( a*b for a, b in
		it.izip([3600, 60, 1], map(float, [hrs, mins, secs])) )


class VodFileCache(object):

	update_lock = True

	def __init__(self, prefix, ext):
		self.path = '{}.{}'.format(prefix, ext)

	@property
	def cached(self):
		if not exists(self.path): return None
		with open(self.path, 'rb') as src: return src.read()

	def update(self, data):
		assert not self.update_lock
		with open(self.path, 'wb') as dst: dst.write(data)
		return data

	def __enter__(self):
		assert self.update_lock
		self.update_lock = False
		return self

	def __exit__(self, *err):
		self.update_lock = True


req_debug = os.environ.get('TVF_REQ_DEBUG')

@contextmanager
def req(method, *args, **kws):
	if not getattr(req, 's', None):
		# Mostly for cases when aria2c lags on startup
		from requests.packages.urllib3.util.retry import Retry
		req.s = requests.Session()
		req.s.mount( 'http://',
			requests.adapters.HTTPAdapter(
				max_retries=Retry(total=4, backoff_factor=1) ) )
	s = kws.pop('session', req.s) or requests

	with closing(s.request(method, *args, **kws)) as r:
		try: r.raise_for_status()
		except Exception as err:
			log_lines(log.error, [
				'HTTP request failed:',
				('  args: %s', args), ('  kws: %s', kws),
				('  response content: %s', r.content) ])
			raise
		yield r

req_jrpc_uid = lambda _ns=get_uid(),\
	_seq=iter(xrange(1, 2**30)): '{}.{}'.format(_ns, next(_seq))

def req_jrpc(url, method, *params, **req_kws):
	req_uid = req_jrpc_uid()
	data_req = dict(
		jsonrpc='2.0', id=req_uid,
		method=method, params=params )
	if req_debug:
		log.debug( 'aria2c rpc request [%s]: %s', req_uid,
			json.dumps(dict(url=url, method=method, data=data_req)) )
	with req('post', url, json=data_req, **req_kws) as r: data_res = r.json()
	assert data_res.get('result') is not None, [data_req, data_res]
	if req_debug: log.debug( 'rpc response [%s]: %s', req_uid, json.dumps(data_res))
	return data_res['result']


def vod_fetch(url, file_prefix,
		start_delay=None, max_length=None, scatter=None, part_file=False,
		ytdl_list_formats=False, ytdl_opts=None, aria2c_opts=None,
		output_format=None, verbose=False, keep_tempfiles=False, dl_info_suffix=None ):

	if ytdl_list_formats:
		log.info('--- Listing formats available for VoD %s (url: %s)', file_prefix, url)
		cmd = ['youtube-dl']
		if verbose: cmd.append('--verbose')
		cmd = cmd + ['--list-formats'] + (ytdl_opts or list()) + [url]
		log.debug('Running "youtube-dl --list-formats" command: %s', ' '.join(cmd))
		subprocess.check_call(cmd, close_fds=True)
		return

	dst_file_part = '{}.part.mp4'.format(file_prefix)

	start_delay = start_delay or 0
	vod_cache = ft.partial(VodFileCache, file_prefix)

	with vod_cache('filename') as vc:
		dst_file = vc.cached
		if not dst_file:
			cmd = ['youtube-dl']
			if verbose: cmd.append('--verbose')
			cmd = cmd + ['--get-filename'] + (ytdl_opts or list())
			if output_format: cmd = cmd + ['--output', output_format]
			cmd = cmd + [url]
			log.debug('Running "youtube-dl --get-filename" command: %s', ' '.join(cmd))
			dst_file = vc.update(subprocess.check_output(cmd, close_fds=not mswindows).strip())

		if exists(dst_file):
			log.info('--- Skipping download for existing file: %s (rename/remove it to force)', dst_file)

			if not keep_tempfiles:
				os.unlink(vc.path)

			return
		else:
			dst_dir = dirname(dst_file)
			try:
				os.makedirs(dst_dir)
				log.info('--- Created directory %s', dst_dir)
			except OSError:
				if not isdir(dst_dir):
					raise

	log.info('--- Downloading VoD %s (url: %s)%s', file_prefix, url, dl_info_suffix or '')

	with vod_cache('m3u8.url') as vc:
		url_pls = vc.cached
		if not url_pls:
			cmd = ['youtube-dl']
			if verbose: cmd.append('--verbose')
			cmd = cmd + ['--get-url'] + (ytdl_opts or list()) + [url]
			log.debug('Running "youtube-dl --get-url" command: %s', ' '.join(cmd))
			url_pls = vc.update(subprocess.check_output(cmd, close_fds=not mswindows).strip())
	assert ' ' not in url_pls, url_pls
	url_base = url_pls.rsplit('/', 1)[0]

	with vod_cache('m3u8.ua') as vc:
		ua = vc.cached
		if not ua:
			ua = vc.update(subprocess.check_output(
				['youtube-dl', '--dump-user-agent'], close_fds=not mswindows ).strip())

	with vod_cache('m3u8') as vc:
		pls = vc.cached
		if not pls:
			log.debug('Fetching playlist from URL: %s', url_pls)
			with req('get', url_pls, headers={'user-agent': ua}) as r: pls = vc.update(r.text)

	# port/key are always updated between aria2c runs
	with vod_cache('rpc_key') as vc: key = vc.update(get_uid(18))
	with vod_cache('rpc_port') as vc:
		with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
			s.bind(('localhost', 0))
			addr, port = s.getsockname()
		port = int(vc.update(bytes(port)))

	# aria2c requires 16-char gid, gid_format fits number in
	#  first 6 chars because it looks nice in (tuncated) aria2c output
	gid_seq = iter(xrange(1, 2**30))
	gid_format, gid_parse = '{:06d}0000000000'.format, lambda gid_str: int(gid_str[:6])
	with vod_cache('gids') as vc:
		gids_done, gids_done_path = vc.cached, vc.path
		if not gids_done: gids_done = vc.update('')
		gids_done = set(it.imap(gid_parse, gids_done.splitlines()))
	gids_started, gids_needed = OrderedDict(), list()

	hook_done = None
	if mswindows:
		hook_done = '{}/.fetch_twitch_vod.{}.done.hook.bat'.format(os.environ['TEMP'], os.getpid())
	else:
		hook_done = '/tmp/.fetch_twitch_vod.{}.done.hook'.format(os.getpid())

	assert "'" not in file_prefix, file_prefix
	assert "'" not in gids_done_path, gids_done_path
	with open(hook_done, 'wb') as dst:
		if mswindows:
			newfilename = '{}.%1.mp4.chunk'.format(os.path.basename(file_prefix))
			dst.write('\n'.join([
				'@echo off',
				'if exist "%~dp3{}" del "%~dp3{}"'.format(newfilename, newfilename),
				'rename %~f3 "{}"'.format(newfilename) + ' && echo %1 >> "{}"'.format(os.path.abspath(gids_done_path)) ]))
		else:
			# Try to pick leaner shell than bash, since hook is dead-simple
			# sh hook(s) assume that gids_*_path fs supports atomic O_APPEND
			sh_path = '/bin/dash'
			if not exists(sh_path): sh_path = '/bin/ash' # busybox
			if not exists(sh_path): sh_path = '/bin/sh'

			dst.write('\n'.join([
				'#!{}'.format(sh_path),
				'mv "$3" \'{}\'."$1".mp4.chunk\\'.format(file_prefix),
				'  && echo "$1" >>\'{}\''.format(gids_done_path) ]))
			os.fchmod(dst.fileno(), 0700)

	aria2c_log_level = 'notice' if verbose else 'warn'
	cmd = [
			'aria2c',

			'--summary-interval=0',
			'--console-log-level={}'.format(aria2c_log_level),

			'--stop-with-process={}'.format(os.getpid()),
			'--enable-rpc=true',
			'--rpc-listen-port={}'.format(port),
			'--rpc-secret={}'.format(key),

			'--no-netrc',
			'--always-resume=false',
			'--max-concurrent-downloads=5',
			'--max-connection-per-server=5',
			'--max-file-not-found=5',
			'--max-tries=8',
			'--timeout=15',
			'--connect-timeout=10',
			'--lowest-speed-limit=100K',
			'--user-agent={}'.format(ua),

			'--on-download-complete={}'.format(hook_done),
		] + (aria2c_opts or list())
	log.debug('Starting aria2c daemon: %s', ' '.join(cmd))
	aria2c = subprocess.Popen(cmd, close_fds=not mswindows)
	aria2c_exit_clean = False
	aria2c_jrpc = ft.partial(req_jrpc, 'http://localhost:{}/jsonrpc'.format(port))
	key = 'token:{}'.format(key)

	### Make sure that aria2c was started and rpc is working
	for n in xrange(4):
		try: aria2c_jrpc('aria2.getVersion', key, session=None)
		except requests.exceptions.ConnectionError as err:
			if aria2c.poll() != None:
				err = ( 'aria2c exited with error code {}'
					' (see also stderr output above)' ).format(aria2c.wait())
				break
			time.sleep(1)
		else:
			err = None
			break
	if err:
		log.error('Failed to connect to aria2c rpc socket - %s', err)
		return 1

	log.debug('Starting downloads (rpc port: %s)...', port)

	line_buff, queue_batch = list(), 50
	wait_last_gids, poll_delay = 100, 5
	chunk_err_retries, chunk_err_retry_delay = 10, 2
	dst_file_tmp = dst_file_cat = None

	try:
		def queue_gid_downloads(gid_files, pos=None):
			# system.multicall(methods)
			# aria2.addUri([secret], uris[, options[, position]])
			if pos is not None: pos = xrange(pos, 2**30)
			res = aria2c_jrpc('system.multicall', list(
				dict(
					methodName='aria2.addUri',
					params=[ key, ['{}/{}'.format(url_base, path)],
						dict(gid=gid_str, out='{}.{}.mp4.chunk.tmp'.format(file_prefix, gid_str)) ]
						+ ([] if not pos else [next(pos)]) )
				for gid_str, path in gid_files ))
			res_chk = list([gid] for gid, path in gid_files)
			if res != res_chk:
				log_lines(log.error, [
					'Result gid match check failed for submitted urls.',
					('  expected: %s', ', '.join(bytes(r[0]) for r in res_chk)),
					('  returned: %s', ', '.join(( bytes(r[0])
						if isinstance(r, list) else repr(r) ) for r in res)) ])
				raise RuntimeError('Result gid match check failed')

		def line_buff_flush():
			gid_files = list((next(gid_seq), path) for path in line_buff)
			gids_needed.extend(gid_files)
			gid_files = list((gid, path) for gid, path in gid_files if gid not in gids_done)
			gids_started.update(gid_files)
			gid_files = list((gid_format(gid), path) for gid, path in gid_files)
			if gid_files: queue_gid_downloads(gid_files)
			del line_buff[:]

		def scatter_iter():
			(a, b), res = scatter, True
			while True:
				td = yield res
				if a > 0: a -= td
				if a <= 0: res = False
				b -= td
				if b <= 0: (a, b), res = scatter, True

		### Queue all initial downloads
		scatter_chk = scatter and scatter_iter()
		if scatter_chk: next(scatter_chk)
		for line in pls.splitlines():
			m = re.search(r'^#EXTINF:([\d.]+),', line)
			if m:
				td = float(m.group(1))
				start_delay -= td
			if start_delay > 0: continue
			if not line or line.startswith('#'): continue
			if max_length and max_length + start_delay < 0: break
			if scatter_chk and not scatter_chk.send(td): continue
			line_buff.append(line)
			if len(line_buff) >= queue_batch: line_buff_flush()
		if line_buff: line_buff_flush()

		### Init/queue stuff to assemble part-file
		chunks_needed = list( (gid_str, '{}.{}.mp4.chunk'.format(file_prefix, gid_str), path)
			for gid_str, path in ((gid_format(gid), path) for gid, path in sorted(gids_needed)) )
		if part_file: dst_file_pos, dst_file_tmp = 0, open(dst_file_part, 'wb')

		### Wait-to-complete - check missing - retry loop
		gid_retries, gids_started_count = None, len(gids_started)
		gid_last = list(gids_started)[-1] if gids_started else 0
		log.info( '\n\n  ------ Started %s downloads,'
			' last gid: %06d ------  \n', gids_started_count, gid_last )
		for n in xrange(1, chunk_err_retries+1):
			if gid_retries:
				aria2c_jrpc('aria2.purgeDownloadResult', key)
				for chunk in it_adjacent(gid_retries, queue_batch):
					queue_gid_downloads(chunk)

			while True:
				dst_file_qe, dst_file_queue, dst_file_retries = False, list(), list()
				if dst_file_tmp:
					## Append new chunks to dst_file_tmp
					if dst_file_cat and dst_file_cat.poll() is not None:
						if dst_file_cat.wait():
							log.error('"cat" subprocess failed, dst file: %r', dst_file_tmp.name)
							return 1
						dst_file_cat = None
					if not dst_file_cat:
						for gid_str, chunk, path in it.islice(chunks_needed, dst_file_pos, None):
							if exists(chunk) and not dst_file_qe:
								dst_file_queue.append(chunk)
								continue
							s = aria2c_jrpc('aria2.tellStatus', key, gid_str, ['status']).get('status')
							if s == 'active': break
							elif s == 'waiting': aria2c_jrpc('aria2.changePosition', key, gid_str, 0, 'POS_SET')
							elif s == 'error': dst_file_retries.append((gid_str, path))
							dst_file_qe = True
						if dst_file_retries: # XXX: redundant with gid_retries around it
							for chunk in it_adjacent(dst_file_retries, queue_batch):
								aria2c_jrpc('system.multicall', list(
									dict(methodName='aria2.removeDownloadResult', params=[key, gid_str])
									for gid_str, path in chunk ))
								queue_gid_downloads(chunk, 0)
						if dst_file_queue:
							dst_file_pos += len(dst_file_queue)
							dst_file_cat = subprocess.Popen(
								['cat'] + dst_file_queue, stdout=dst_file_tmp, close_fds=True )

				## Wait until all downloads are attempted by aria2c
				gids_wait = len(aria2c_jrpc('aria2.tellActive', key, ['status']))
				res = aria2c_jrpc('aria2.tellWaiting', key, 0, wait_last_gids, ['status'])
				if len(res) == wait_last_gids: gids_wait = '>{}'.format(wait_last_gids)
				else: gids_wait += len(res)
				if not gids_wait: break
				log_parts = '' if not dst_file_tmp else\
					', part-retries/appends: {}'.format(
						'append-in-progress' if dst_file_cat and not dst_file_queue
						else '{} / {}'.format(len(dst_file_retries), len(dst_file_queue)) )
				log.debug( # helps to see the overall progress
					'\n\n  ------ waiting for downloads (count: %s / %s,'
						' last gid: %06d, err-retry-pass: %s / %s%s) ------  \n',
					gids_wait, gids_started_count, gid_last, n, chunk_err_retries, log_parts )
				time.sleep(poll_delay)

			## Generic "gid_retries" retry-loop, XXX: redundant with dst_file_retries
			with vod_cache('gids') as vc:
				for gid in it.imap(gid_parse, vc.cached.splitlines()): gids_started.pop(gid, None)
			gid_retries = list((gid_format(gid), path) for gid, path in gids_started.viewitems())
			if not gid_retries: break
			gid_last = list(gids_started)[-1]
			log.debug('Chunk retry delay (failed: %s): %ss', len(gid_retries), chunk_err_retry_delay)
			time.sleep(chunk_err_retry_delay)

		else:
			log.error('Failed to download %s chunks (after %s retries)', len(gid_retries), n)

		### Proper shutdown
		aria2c_jrpc('aria2.shutdown', key)
		aria2c_exit_clean = not gid_retries
		log.debug(
			'Finished with downloads (%s chunks downloaded, %s failed, %s existing)',
			gids_started_count - len(gid_retries), len(gid_retries), len(gids_done) )

	finally:
		if not aria2c_exit_clean:
			if dst_file_cat: dst_file_cat.terminate()
			aria2c.terminate()
		if dst_file_tmp: dst_file_tmp.close()
		if dst_file_cat: dst_file_cat.wait()
		aria2c.wait()
		os.unlink(hook_done)

	if not aria2c_exit_clean:
		log.error('Unresolved download errors detected, aborting')
		return 1

	chunks_needed = map(op.itemgetter(1), chunks_needed)
	chunks = filter(exists, chunks_needed)
	chunks_missing = set(chunks_needed).difference(chunks)
	if chunks_missing:
		log.error(
			'Aborting due to %s missing chunk(s)'
				' (use --debug for full list, fix/remove %r to re-download)',
			len(chunks_missing), gids_done_path )
		log_lines( log.debug, ['Missing chunks:']
			+ list(('  %s', chunk) for chunk in sorted(chunks_missing)) )
		return 1

	if dst_file_tmp:
		log.info('Renaming part-file (%s chunks) to destination: %r', len(chunks), dst_file)
		os.rename(dst_file_tmp.name, dst_file)
	else:
		log.info('Concatenating %s chunk files to: %r', len(chunks), dst_file)
		with open(dst_file_part, 'wb') as dst:
			if mswindows:
				for chunk in chunks:
					shutil.copyfileobj(open(chunk, "rb"), dst)
			else:
				subprocess.check_call(['cat'] + chunks, stdout=dst, close_fds=not mswindows)
		os.rename(dst_file_part, dst_file)

	if not keep_tempfiles:
		tmp_files = list(it.chain(( vod_cache(ext).path for ext in
				['filename', 'm3u8.url', 'm3u8.ua', 'm3u8', 'rpc_key', 'rpc_port', 'gids'] ), chunks))
		log.debug('Cleaning up temporary files (count: %s)...', len(tmp_files))
		for p in tmp_files: os.unlink(p)

	log.info('Finished, resulting file: %s', dst_file)


def main(args=None):
	import argparse
	parser = argparse.ArgumentParser(
		usage='%(prog)s [options] url file_prefix [url-2 file_prefix-2 ...]',
		description='Grab a VoD or a specified slice of it from twitch.tv, properly.')
	parser.add_argument('url', help='URL for a VoD to fetch.')
	parser.add_argument('file_prefix', help='File prefix to assemble temp files under.')
	parser.add_argument('more_url_and_prefix_pairs', nargs='*',
		help='Any number of extra "url file_prefix" arguments can be specified.')

	parser.add_argument('-F', '--ytdl-list-formats',
		action='store_true', help='Do not download anything,'
			' just list formats available for each specified URL and exit.')
	parser.add_argument('-y', '--ytdl-opts',
		action='append', metavar='opts',
		help='Extra opts for youtube-dl --get-url and --get-filename commands.'
			' Will be split on spaces, unless option is used multiple times.')
	parser.add_argument('-a', '--aria2c-opts',
		action='append', metavar='opts',
		help='Extra opts for aria2c command.'
			' Will be split on spaces, unless option is used multiple times.')

	parser.add_argument('-o', '--output-format',
		metavar='output_format',
		help='Output file name template. Passed to youtube-dl --get-filename'
				' as the --output argument to get the final file name for the video.'
			' If not specified, youtube-dl\'s default format will be used.'
			' If multiple url/prefix args are specified, this option will be applied to all of them.')
	parser.add_argument('-s', '--start-pos',
		metavar='[[hours:]minutes:]seconds',
		help='Only download video chunks after specified start position.'
			' If multiple url/prefix args are specified, this option will be applied to all of them.')
	parser.add_argument('-l', '--length',
		metavar='[[hours:]minutes:]seconds',
		help='Only download specified length of the video (from specified start or beginning).'
			' If multiple url/prefix args are specified, this option will be applied to all of them.')
	parser.add_argument('-x', '--scatter',
		metavar='[[hours:]minutes:]seconds/[[hours:]minutes:]seconds',
		help='Out of whole video (or a chunk specified by --start and --length),'
				' download only every N seconds (or mins/hours) out of M.'
			' E.g. "1:00/10:00" spec here will download 1 first min of video out of every 10.'
			' Idea here is to produce something like preview of the video to allow'
				' to easily narrow down which part of it is interesting and worth downloading in full.')

	parser.add_argument('-p', '--create-part-file',
		action='store_true', help='Create partial-download *.part.mp4 file'
				' and append chunks (in a strict order) to it as they get'
				' downloaded instead of assembling it at the very end.'
			' Allows to start playback before all chunks arrive,'
				' but part-file has to be re-assembled from pieces on every script restart.')
	parser.add_argument('-k', '--keep-tempfiles',
		action='store_true', help='Do not remove all the'
				' temporary files after successfully assembling resulting mp4.'
			' Chunks in particular might be useful to download different but overlapping video slices.')

	parser.add_argument('--debug', action='store_true', help='Verbose operation mode.')
	opts = parser.parse_args(sys.argv[1:] if args is None else args)

	global log
	import logging
	logging.basicConfig(
		datefmt='%Y-%m-%d %H:%M:%S',
		format='%(asctime)s :: %(name)s %(levelname)s :: %(message)s',
		level=logging.DEBUG if opts.debug else logging.INFO )
	log = logging.getLogger('main')

	# Retries logged from here are kinda useless, especially before aria2c starts
	logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.ERROR)

	ytdl_opts = opts.ytdl_opts or list()
	if len(ytdl_opts) == 1: ytdl_opts = ytdl_opts[0].strip().split()
	aria2c_opts = opts.aria2c_opts or list()
	if len(aria2c_opts) == 1: aria2c_opts = aria2c_opts[0].strip().split()

	scatter = opts.scatter
	if scatter:
		scatter = map(parse_pos_spec, scatter.split('/', 1))
		assert len(scatter) == 2, [opts.scatter, scatter]

	dl_kws = dict(
		start_delay=parse_pos_spec(opts.start_pos) if opts.start_pos else 0,
		max_length=opts.length and parse_pos_spec(opts.length),
		scatter=scatter, part_file=opts.create_part_file,
		ytdl_list_formats=opts.ytdl_list_formats,
		ytdl_opts=ytdl_opts, aria2c_opts=aria2c_opts,
		output_format=opts.output_format, verbose=opts.debug, keep_tempfiles=opts.keep_tempfiles )

	vod_queue, args = list(),\
		[opts.url, opts.file_prefix] + (opts.more_url_and_prefix_pairs or list())
	if len(args) % 2:
		parser.error( 'Odd number of url/prefix args specified'
			' ({}), while these should always come in pairs.'.format(len(args)) )
	for url, prefix in it_adjacent(args, 2):
		if re.search(r'^https?:', prefix):
			if re.search(r'^https?:', url):
				parser.error( 'Both url/file_prefix args seem'
					' to be an URL, only first one should be: {} {}'.format(url, prefix) )
			prefix, url = url, prefix
			log.warn( 'Looks like url/prefix args got'
				' mixed-up, correcting that to prefix=%s url=%s', prefix, url )
		if not re.search(r'^https?://[^/]+/[^/]+/v/', url):
			parser.error( 'Provided URL appears to be for the'
				' unsupported VoD format (only /v/ VoDs are supported): {}'.format(url) )
		vod_queue.append((url, prefix))

	for n, (url, prefix) in enumerate(vod_queue, 1):
		info_suffix = None if len(vod_queue) == 1 else ' [{} / {}]'.format(n, len(vod_queue))
		vod_fetch(url, prefix, dl_info_suffix=info_suffix, **dl_kws)

if __name__ == '__main__': sys.exit(main())
