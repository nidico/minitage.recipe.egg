#!/usr/bin/env python

# Copyright (C) 2008, Mathieu PASQUET <kiorky@cryptelium.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

__docformat__ = 'restructuredtext en'

import datetime
import imp
import logging
import os
import setuptools.archive_util
import sha
import shutil
import sys
import tempfile
import urllib2
import urlparse

from minitage.core.common import get_from_cache, system
from minitage.core.unpackers.interfaces import IUnpackerFactory
from minitage.core import core
from minitage.core import interfaces

class MinitageCommonRecipe(object):
    """
    Downloads and installs a distutils Python distribution.
    """
    def __init__(self, buildout, name, options):
        self.logger = logging.getLogger(name)
        self.buildout, self.name, self.options = buildout, name, options
        self.uname = os.uname()[0]
        self.offline = buildout.offline
        self.install_from_cache = self.options.get('install-from-cache', None)

        # to the python (which will install the egg) version !
        self.executable = options.get('executable', sys.executable)
        self.executable_version = os.popen(
            '%s -c "%s"' % (
                self.executable ,
                'import sys;print sys.version[:3]'
            )
        ).read().replace('\n', '')

        # site-packages defaults
        self.site_packages = 'site-packages-%s' % self.executable_version
        self.site_packages_path = self.options.get(
            'site-packages',
            os.path.join(
                self.buildout['buildout']['directory'],
                self.site_packages)
        )

        # url from
        self.url = self.options['url']

        # maybe md5
        self.md5 = self.options.get('md5sum', None)

        # destination
        options['location'] = os.path.join(
            buildout['buildout']['parts-directory'],
            options['name']
        )
        self.prefix = options['location']

        self.configure = options.get('configure', 'configure')

        # If 'download-cache' has not been specified,
        # fallback to [buildout]['downloads']
        buildout['buildout'].setdefault(
            'download-cache',
            buildout['buildout'].get(
                'download-cache',
                os.path.join(
                    buildout['buildout']['directory'],
                    'downloads'
                )
            )
        )

        # separate archives in downloaddir/minitage
        self.download_cache = os.path.join(
            buildout['buildout']['directory'],
            buildout['buildout'].get('download-cache'),
            'minitage'
        )

        # do we install cextension stuff
        self.build_ext = self.options.get('build_ext', '')

        # patches stuff
        self.patch_cmd = self.options.get(
            'patch-binary',
            'patch'
        ).strip()

        self.patch_options = ' '.join(
            self.options.get(
                'patch-options', '-p0'
            ).split()
        )
        self.patches = self.options.get('patches', '').split()
        # os name.
        self.uname=os.uname()[0]
        # conditionnaly add OS specifics patches.
        self.patches.extend(
            self.options.get(
                '%s-patches' % (self.uname.lower()),
                ''
            ).split()
        )

        # if gmake is setted. taking it as the make cmd !
        # be careful to have a 'gmake' in your path
        # we have to make it only in non linux env.
        # if wehave gmake setted, use gmake too.
        gnumake = 'make'
        if self.uname != 'Linux' \
           or self.buildout.get('part', {}).get('gmake', None):
            gnumake = 'gmake'
        self.make_cmd = self.options.get('make-binary', gnumake).strip()

        # what we will install.
        # if 'make-targets'  present, we get it line by line
        # and all target must be specified
        # We will default to make '' and make install
        self.make_targets = self.options.get(
            'make-targets',
            '\n'
        ).split('\n')
        self.install_targets = self.options.get(
            'make-install-targets',
            'install'
        ).split('\n')

        # configuration options
        self.configure_options = ' '.join(
            self.options.get(
                'configure-options',
                '').split()
        )
        # conditionnaly add OS specifics patches.
        self.configure_options += ' '.join(
            self.options.get(
                'configure-options-%s' % (self.uname.lower()),
                ''
            ).split()
        )

        #path
        self.path = self.options.get('path', '').split()

        # pkgconfigpath
        self.pkgconfigpath = self.options.get('pkgconfigpath', '').split()

        # python path
        self.pypath = [self.buildout['buildout']['directory'], self.options['location']]
        self.pypath.extend(self.pypath)
        #self.pypath.extend(os.environ.get('PYTHONPATH','').split(':'))
        self.pypath.extend(self.options.get('pythonpath', '').split())

        # compilation flags
        self.includes = self.options.get('includes', '').split()
        self.libraries = self.options.get('libraries', '').split()
        self.rpath = self.options.get('rpath', '').split()

        # tmp dir
        self.tmp_directory = os.path.join(
            buildout['buildout'].get('directory'),
            '__minitage__%s__tmp' % name
        )

        # build directory
        self.build_dir = self.options.get('build-dir', None)

        # cleaning if we have a prior compilation step.
        if os.path.isdir(self.tmp_directory):
            self.logger.info(
                'Removing pre existing temporay directory: %s' % self.tmp_directory
            )
            shutil.rmtree(self.tmp_directory)

        # minitage specific
        self.minitage_section = {}
        self.minitage_dependencies = []
        self.minitage_eggs = []
        if 'minitage' in buildout:
            self.minitage_section = buildout['minitage']

        self.minitage_dependencies.extend(
            [os.path.abspath(os.path.join(
                buildout['buildout']['directory'],
                '..',
                '..',
                'dependencies',
                s,
                'parts',
                'part'
            )) for s in self.minitage_section.get(
                'dependencies', ''
            ).split() if s.strip()]
        )

        self.minitage_eggs.extend(
            [os.path.abspath(os.path.join(
                buildout['buildout']['directory'],
                '..', '..', 'eggs', s, 'parts', self.site_packages,
            )) for s in self.minitage_section.get(
                  'eggs', '').split() if s.strip()]
        )

        for s in self.minitage_dependencies:
            self.includes.append(os.path.join(s, 'include'))
            self.libraries.append(os.path.join(s, 'lib'))
            self.rpath.append(os.path.join(s, 'lib'))
            self.pkgconfigpath.append(os.path.join(s, 'lib', 'pkgconfig'))

        for s in self.minitage_eggs + [self.site_packages_path]:
            self.pypath.append(s)

    def _choose_configure(self, compile_dir):
        """configure magic to runne with
        exotic configure systems.
        """
        if self.build_dir:
            if not os.path.isdir(self.build_dir):
                os.makedirs(self.build_dir)
        else:
            self.build_dir = compile_dir

        configure = os.path.join(compile_dir, self.configure)
        if not os.path.isfile(configure) \
           and (not 'noconfigure' in self.options):
            self.logger.error('Unable to find the configure script')
            raise core.MinimergeError('Invalid package contents, there is no configure script.')

        return configure

    def _configure(self, configure):
        """Run configure script.
        Argument
            - configure : the configure script
        """
        cwd = os.getcwd()
        os.chdir(self.build_dir)
        if not 'noconfigure' in self.options:
            self._system(
                    '%s --prefix=%s %s' % (
                        configure,
                        self.prefix,
                        self.configure_options
                    )
                )
        os.chdir(cwd)

    def _make(self, directory, targets):
        """Run make targets except install."""
        cwd = os.getcwd()
        os.chdir(directory)
        for target in targets:
            try:
                self._system('%s %s' % (self.make_cmd, target))
            except Exception, e:
                message = 'Make failed for targets: %s' % targets
                raise core.MinimergeError(message)
        os.chdir(cwd)

    def _make_install(self, directory):
        """"""
        # moving and restoring if problem :)
        cwd = os.getcwd()
        os.chdir(directory)
        tmp = '%s.old' % self.prefix
        if os.path.isdir(self.prefix):
            shutil.move(self.prefix, tmp)

        if not 'noinstall' in self.options:
            try:
                os.makedirs(self.prefix)
                self._call_hook('pending-make-install-hook')
                self._make(directory, self.install_targets)
            except Exception, e:
                shutil.rmtree(self.prefix)
                shutil.move(tmp, self.prefix)
                raise core.MinimergeError('Install failed:\n\t%s' % e)
        if os.path.exists(tmp):
             shutil.rmtree(tmp)
        os.chdir(cwd)

    def _download(self):
        """Download the archive."""
        self.logger.info('Download archive')
        return get_from_cache(
            self.url,
            self.download_cache,
            self.logger,
            self.md5,
            self.offline,
        )

    def _set_py_path(self):
        """Set python path."""
        self.logger.info('Setting path')
        os.environ['PYTHONPATH'] = ':'.join(self.pypath)

    def _set_path(self):
        """Set path."""
        self.logger.info('Setting path')
        os.environ['PATH'] = ':'.join(
            self.path
            + [self.buildout['buildout']['directory']]
            + [self.options['location']]
            + os.environ.get('PATH', '').split(':')
        )

    def _set_pkgconfigpath(self):
        """Set PKG-CONFIG-PATH."""
        self.logger.info('Setting pkgconfigpath')
        os.environ['PKG_CONFIG_PATH'] = ':'.join(
            self.pkgconfigpath
            + os.environ.get('PKG_CONFIG_PATH', '').split(':')
        )

    def _set_compilation_flags(self):
        """Set CFALGS/LDFLAGS."""
        self.logger.info('Setting compilation flags')
        if self.rpath:
            os.environ['LD_RUN_PATH'] = ':'.join(
                [os.environ.get('LD_RUN_PATH','')]
                + [s for s in self.rpath\
                   if s.strip()]
                + [os.path.join(self.prefix, 'lib')]
            )

        if self.libraries:
            darwin_ldflags = ''
            if self.uname == 'Darwin':
                # need to cut backward comatibility in the linker
                # to get the new rpath feature present
                # >= osx Leopard
                darwin_ldflags = ' -mmacosx-version-min=10.5.0 '
            os.environ['LDFLAGS'] = ' '.join(
                [os.environ.get('LDFLAGS',' ')]
                + [' -L%s -Wl,-rpath -Wl,%s ' % (s,s) \
                   for s in self.libraries \
                   + [os.path.join(self.prefix, 'lib')]
                   if s.strip()]
                + [darwin_ldflags]
            )

        if self.includes:
            b_cflags = [' -I%s ' % s \
                        for s in self.includes\
                        if s.strip()]
            os.environ['CFLAGS']  =' '.join([
                os.environ.get('CFLAGS',' ')]   + b_cflags
            )
            os.environ['CPPFLAGS']=' '.join([
                os.environ.get('CPPFLAGS',' ')] + b_cflags
            )
            os.environ['CXXFLAGS']=' '.join([
                os.environ.get('CXXFLAGS',' ')] + b_cflags
            )

    def _unpack(self, fname):
        """Unpack something"""
        self.logger.info('Unpacking')
        unpack_f = IUnpackerFactory()
        u = unpack_f(fname)
        u.unpack(fname, self.tmp_directory)

    def _patch(self, directory):
        """Aplying patches in pwd directory."""
        if self.patches:
            self.logger.info('Applying patches.')
            cwd = os.getcwd()
            os.chdir(directory)
            for patch in self.patches:
                 system('%s -t %s < %s' %
                        (self.patch_cmd,
                         self.patch_options,
                         patch),
                        self.logger
                       )
            os.chdir(cwd)

    def update(self):
        pass

    def _call_hook(self, hook):
        """
        This method is copied from z3c.recipe.runscript.
        See http://pypi.python.org/pypi/z3c.recipe.runscript for details.
        """
        if hook in self.options \
           and len(self.options[hook].strip()) > 0:
            self.logger.info('Executing %s' % hook)
            script = self.options[hook]
            filename, callable = script.split(':')
            filename = os.path.abspath(filename)
            module = imp.load_source('script', filename)
            getattr(module, callable.strip())(
                self.options, self.buildout
            )

    def _get_compil_dir(self, directory):
        """Get the compilation directory after creation.
        Basically, the first repository in the directory
        which is not the download cache.
        Arguments:
            - directory where we will compile.
        """
        self.logger.info('Guessing compilation directory')
        contents = os.listdir(directory)
        # remove download dir
        if '.download' in contents:
            del contents[contents. index('.download')]
        return os.path.join(directory, contents[0])

    def _build_python_package(self, directory):
        """Compile a python package."""
        cwd = os.getcwd()
        os.chdir(directory)
        cmds = []
        self._set_py_path()
        # compilation phase if we have an extension module.
        if self.build_ext \
           or self.options.get('rpath',None) \
           or self.options.get('libraries',None) \
           or self.options.get('includes',None):
            self._set_compilation_flags()
            cmds.append(
                '"%s" setup.py build_ext %s' % (
                    self.executable,
                    self.build_ext.replace('\n',' ')
                )
            )

        # build package
        cmds.append('"%s" setup.py build' % (self.executable))

        for cmd in cmds:
            self._system(cmd)

        os.chdir(cwd)

    def _install_python_package(self, directory):
        """Install a python package."""
        self._set_py_path()
        cmd = '"%s" setup.py install %s  %s %s' % (
            self.executable,
            '--install-purelib="%s"' % self.site_packages_path,
            '--install-platlib="%s"' % self.site_packages_path,
            '--prefix=%s' % self.buildout['buildout']['directory']
        )
        # moving and restoring if problem :)
        cwd = os.getcwd()
        os.chdir(directory)
        tmp = '%s.old' % self.site_packages_path
        if os.path.exists(self.site_packages_path):
            shutil.move(self.site_packages_path, tmp)

        if not self.options.get('noinstall', None):
            try:
                os.makedirs(self.site_packages_path)
                self._system(cmd)
            except Exception, e:
                shutil.rmtree(self.site_packages_path)
                shutil.move(tmp, self.site_packages_path)
                raise core.MinimergeError('PythonPackage Install failed:\n\t%s' % e)
        if os.path.exists(tmp):
            shutil.rmtree(tmp)
        os.chdir(cwd)

    def _system(self, cmd):
        """Running a command."""
        self.logger.info('Running %s' % cmd)
        ret = os.system(cmd)
        if ret:
            raise  core.MinimergeError('Command failed: %s' % cmd)
# vim:set et sts=4 ts=4 tw=80:
