# Authors:
#   Jason Gerard DeRose <jderose@redhat.com>
#
# Copyright (C) 2008  Red Hat
# see file 'COPYING' for use and warranty information
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; version 2 only
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

"""
Unit tests for `ipalib.public` module.
"""

from tstutil import raises, getitem, no_set, no_del, read_only
from ipalib import public, plugable, errors


def test_cmd():
	cls = public.cmd
	assert issubclass(cls, plugable.Plugin)
	assert cls.proxy is public.cmd_proxy


def test_obj():
	cls = public.obj
	assert issubclass(cls, plugable.Plugin)


def test_attr():
	cls = public.attr
	assert issubclass(cls, plugable.Plugin)

	class api(object):
		obj = dict(user='the user obj')

	class user_add(cls):
		pass

	i = user_add()
	assert read_only(i, 'obj_name') == 'user'
	assert read_only(i, 'attr_name') == 'add'
	assert read_only(i, 'obj') is None
	i.finalize(api)
	assert read_only(i, 'api') is api
	assert read_only(i, 'obj') == 'the user obj'


def test_mthd():
	cls = public.mthd
	assert issubclass(cls, public.attr)
	assert issubclass(cls, public.cmd)


def test_prop():
	cls = public.prop
	assert issubclass(cls, public.attr)
