# Authors: Karl MacMillan <kmacmillan@mentalrootkit.com>
#
# Copyright (C) 2007  Red Hat
# see file 'COPYING' for use and warranty information
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import logging, sys
import os
import tempfile
from ipapython import sysrestore
from ipapython import ipautil
from ipalib import errors
import ldap
from ipaserver import ipaldap
import base64
import time
import datetime

SERVICE_LIST = {
    'KDC':('krb5kdc', 10),
    'KPASSWD':('ipa_kpasswd', 20),
    'DNS':('named', 30),
    'HTTP':('httpd', 40),
    'CA':('pki-cad', 50)
}

def stop(service_name, instance_name=""):
    ipautil.run(["/sbin/service", service_name, "stop", instance_name])

def start(service_name, instance_name=""):
    ipautil.run(["/sbin/service", service_name, "start", instance_name])

def restart(service_name, instance_name=""):
    ipautil.run(["/sbin/service", service_name, "restart", instance_name])

def is_running(service_name, instance_name=""):
    ret = True
    try:
        ipautil.run(["/sbin/service", service_name, "status", instance_name])
    except ipautil.CalledProcessError:
        ret = False
    return ret

def chkconfig_on(service_name):
    ipautil.run(["/sbin/chkconfig", service_name, "on"])

def chkconfig_off(service_name):
    ipautil.run(["/sbin/chkconfig", service_name, "off"])

def chkconfig_add(service_name):
    ipautil.run(["/sbin/chkconfig", "--add", service_name])

def chkconfig_del(service_name):
    ipautil.run(["/sbin/chkconfig", "--del", service_name])

def is_enabled(service_name):
    (stdout, stderr, returncode) = ipautil.run(["/sbin/chkconfig", "--list", service_name])

    runlevels = {}
    for runlevel in range(0, 7):
        runlevels[runlevel] = False

    for line in stdout.split("\n"):
        parts = line.split()
        if parts[0] == service_name:
            for s in parts[1:]:
                (runlevel, status) = s.split(":")[0:2]
                try:
                    runlevels[int(runlevel)] = status == "on"
                except ValueError:
                    pass
            break

    return (runlevels[3] and runlevels[4] and runlevels[5])

def print_msg(message, output_fd=sys.stdout):
    logging.debug(message)
    output_fd.write(message)
    output_fd.write("\n")


class Service:
    def __init__(self, service_name, sstore=None, dm_password=None):
        self.service_name = service_name
        self.steps = []
        self.output_fd = sys.stdout
        self.dm_password = dm_password

        if sstore:
            self.sstore = sstore
        else:
            self.sstore = sysrestore.StateFile('/var/lib/ipa/sysrestore')

    def _ldap_mod(self, ldif, sub_dict = None):
        assert self.dm_password is not None

        fd = None
        path = ipautil.SHARE_DIR + ldif

        if sub_dict is not None:
            txt = ipautil.template_file(path, sub_dict)
            fd = ipautil.write_tmp_file(txt)
            path = fd.name

        [pw_fd, pw_name] = tempfile.mkstemp()
        os.write(pw_fd, self.dm_password)
        os.close(pw_fd)

        args = ["/usr/bin/ldapmodify", "-h", "127.0.0.1", "-xv",
                "-D", "cn=Directory Manager", "-y", pw_name, "-f", path]

        try:
            try:
                ipautil.run(args)
            except ipautil.CalledProcessError, e:
                logging.critical("Failed to load %s: %s" % (ldif, str(e)))
        finally:
            os.remove(pw_name)

        if fd is not None:
            fd.close()

    def move_service(self, principal):
        """
        Used to move a principal entry created by kadmin.local from
        cn=kerberos to cn=services
        """
        dn = "krbprincipalname=%s,cn=%s,cn=kerberos,%s" % (principal, self.realm, self.suffix)
        try:
            conn = ipaldap.IPAdmin("127.0.0.1")
            conn.simple_bind_s("cn=directory manager", self.dm_password)
        except Exception, e:
            logging.critical("Could not connect to the Directory Server on %s: %s" % (self.fqdn, str(e)))
            raise e
        try:
            entry = conn.getEntry(dn, ldap.SCOPE_BASE)
        except errors.NotFound:
            # There is no service in the wrong location, nothing to do.
            # This can happen when installing a replica
            conn.unbind()
            return
        newdn = "krbprincipalname=%s,cn=services,cn=accounts,%s" % (principal, self.suffix)
        hostdn = "fqdn=%s,cn=computers,cn=accounts,%s" % (self.fqdn, self.suffix)
        conn.deleteEntry(dn)
        entry.dn = newdn
        classes = entry.getValues("objectclass")
        classes = classes + ["ipaobject", "ipaservice", "pkiuser"]
        entry.setValues("objectclass", list(set(classes)))
        entry.setValue("ipauniqueid", 'autogenerate')
        entry.setValue("managedby", hostdn)
        conn.addEntry(entry)
        conn.unbind()
        return newdn

    def add_cert_to_service(self):
        """
        Add a certificate to a service

        This should be passed in DER format but we'll be nice and convert
        a base64-encoded cert if needed (like when we add certs that come
        from PKCS#12 files.)
        """
        try:
            s = self.dercert.find('-----BEGIN CERTIFICATE-----')
            if s > -1:
                e = self.dercert.find('-----END CERTIFICATE-----')
                s = s + 27
                self.dercert = self.dercert[s:e]
                self.dercert = base64.b64decode(self.dercert)
        except Exception:
            pass
        dn = "krbprincipalname=%s,cn=services,cn=accounts,%s" % (self.principal, self.suffix)
        try:
            conn = ipaldap.IPAdmin("127.0.0.1")
            conn.simple_bind_s("cn=directory manager", self.dm_password)
        except Exception, e:
            logging.critical("Could not connect to the Directory Server on %s: %s" % (self.fqdn, str(e)))
            raise e
        mod = [(ldap.MOD_ADD, 'userCertificate', self.dercert)]
        try:
            conn.modify_s(dn, mod)
        except Exception, e:
            logging.critical("Could not add certificate to service %s entry: %s" % (self.principal, str(e)))
        conn.unbind()

    def is_configured(self):
        return self.sstore.has_state(self.service_name)

    def set_output(self, fd):
        self.output_fd = fd

    def stop(self, instance_name=""):
        stop(self.service_name, instance_name)

    def start(self, instance_name=""):
        start(self.service_name, instance_name)

    def restart(self, instance_name=""):
        restart(self.service_name, instance_name)

    def is_running(self):
        return is_running(self.service_name)

    def chkconfig_add(self):
        chkconfig_add(self.service_name)

    def chkconfig_del(self):
        chkconfig_del(self.service_name)

    def chkconfig_on(self):
        chkconfig_on(self.service_name)

    def chkconfig_off(self):
        chkconfig_off(self.service_name)

    def is_enabled(self):
        return is_enabled(self.service_name)

    def backup_state(self, key, value):
        self.sstore.backup_state(self.service_name, key, value)

    def restore_state(self, key):
        return self.sstore.restore_state(self.service_name, key)

    def print_msg(self, message):
        print_msg(message, self.output_fd)

    def step(self, message, method):
        self.steps.append((message, method))

    def start_creation(self, message, runtime=-1):
        if runtime > 0:
            plural=''
            est = time.localtime(runtime)
            if est.tm_min > 0:
                if est.tm_min > 1:
                    plural = 's'
                self.print_msg('%s: Estimated time %d minute%s' % (message, est.tm_min, plural))
            else:
                if est.tm_sec > 1:
                    plural = 's'
                self.print_msg('%s: Estimated time %d second%s' % (message, est.tm_sec, plural))
        else:
            self.print_msg(message)

        step = 0
        for (message, method) in self.steps:
            self.print_msg("  [%d/%d]: %s" % (step+1, len(self.steps), message))
            s = datetime.datetime.now()
            method()
            e = datetime.datetime.now()
            d = e - s
            logging.debug("  duration: %d seconds" % d.seconds)
            step += 1

        self.print_msg("done configuring %s." % self.service_name)

        self.steps = []

    def __get_conn(self, fqdn, dm_password):
        try:
            conn = ipaldap.IPAdmin("127.0.0.1")
            conn.simple_bind_s("cn=directory manager", dm_password)
        except Exception, e:
            logging.critical("Could not connect to the Directory Server on %s: %s" % (fqdn, str(e)))
            raise e

        return conn

    def ldap_enable(self, name, fqdn, dm_password, ldap_suffix):
        self.chkconfig_off()
        conn = self.__get_conn(fqdn, dm_password)

        entry_name = "cn=%s,cn=%s,%s,%s" % (name, fqdn,
                                            "cn=masters,cn=ipa,cn=etc",
                                            ldap_suffix)
        order = SERVICE_LIST[name][1]
        entry = ipaldap.Entry(entry_name)
        entry.setValues("objectclass",
                        "nsContainer", "ipaConfigObject")
        entry.setValues("cn", name)
        entry.setValues("ipaconfigstring",
                        "enabledService", "startOrder " + str(order))

        try:
            conn.add_s(entry)
        except ldap.ALREADY_EXISTS, e:
            logging.critical("failed to add %s Service startup entry" % name)
            raise e

class SimpleServiceInstance(Service):
    def create_instance(self, gensvc_name=None, fqdn=None, dm_password=None, ldap_suffix=None):
        self.gensvc_name = gensvc_name
        self.fqdn = fqdn
        self.dm_password = dm_password
        self.suffix = ldap_suffix

        self.step("starting %s " % self.service_name, self.__start)
        self.step("configuring %s to start on boot" % self.service_name, self.__enable)
        self.start_creation("Configuring %s" % self.service_name)

    def __start(self):
        self.backup_state("running", self.is_running())
        self.restart()

    def __enable(self):
        self.chkconfig_add()
        self.backup_state("enabled", self.is_enabled())
        if self.gensvc_name == None:
            self.chkconfig_on()
        else:
            self.ldap_enable(self.gensvc_name, self.fqdn,
                             self.dm_password, self.suffix)

    def uninstall(self):
        if self.is_configured():
            self.print_msg("Unconfiguring %s" % self.service_name)

        running = self.restore_state("running")
        enabled = not self.restore_state("enabled")

        if not running is None and not running:
            self.stop()
        if not enabled is None and not enabled:
            self.chkconfig_off()
            self.chkconfig_del()
