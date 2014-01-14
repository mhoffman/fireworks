#!/usr/bin/env python

"""
This module implements a CommonAdaptor that supports standard PBS and SGE
queues.
"""
import getpass
import os
import re
import subprocess
from fireworks.queue.queue_adapter import QueueAdapterBase, Command
from fireworks.utilities.fw_serializers import serialize_fw
from fireworks.utilities.fw_utilities import log_exception, log_fancy

__author__ = 'Anubhav Jain, Michael Kocher, Shyue Ping Ong'
__copyright__ = 'Copyright 2012, The Materials Project'
__version__ = '0.1'
__maintainer__ = 'Anubhav Jain'
__email__ = 'ajain@lbl.gov'
__date__ = 'Dec 12, 2012'


class CommonAdapter(QueueAdapterBase):
    """
    An adapter that works on most PBS (including derivatives such as
    TORQUE), SGE, and SLURM queues.
    """
    _fw_name = 'CommonAdapter'
    supported_q_types = ["PBS", "SGE", "SLURM"]

    def __init__(self, q_type, q_name=None, template_file=None, **kwargs):
        """
        :param q_type: The type of queue. Right now it should be either PBS
                       or SGE.
        :param q_name: A name for the queue. Can be any string.
        :param template_file: The path to the template file. Leave it as
                              None (the default) to use Fireworks' built-in
                              templates for PBS and SGE, which should work
                              on most queues.
        :param **kwargs: Series of keyword args for queue parameters.
        """
        if q_type not in CommonAdapter.supported_q_types:
            raise ValueError(
                "{} is not a supported queue type. "
                "CommonAdaptor supports {}".format(q_type,
                                                   CommonAdapter.supported_q_types))
        self.q_type = q_type
        self.template_file = os.path.abspath(template_file) if template_file is not None else \
            CommonAdapter._get_default_template_file(q_type)
        self.submit_cmd = 'sbatch' if q_type == 'SLURM' else 'qsub'
        self.q_name = q_name if q_name else q_type
        self.update(dict(kwargs))

    def _parse_jobid(self, output_str):
        if self.q_type == 'SLURM': # this special case might not be needed
            return int(output_str.split()[3])
            #This should work regardless of PBS or SGE.
        #PBS: "1234.whatever", SGE: "Your job 44275 ("jobname") has been submitted"
        m = re.search("(\d+)", output_str)
        if m:
            return m.group(1)
        raise RuntimeError("Unable to parse jobid")

    def _get_status_cmd(self, username):
        if self.q_type == 'SLURM':
            return ['squeue', '-o "%u"', '-u', username]
        return ['qstat', '-u', username]

    def _parse_njobs(self, output_str, username):
        # TODO: what if username is too long for the output and is cut off?

        if self.q_type == 'SLURM': # this special case might not be needed
            # TODO: currently does not filter on queue name or job state
            outs = output_str.split('\n')
            return len([line.split() for line in outs if username in line])
        count = 0
        for l in output_str.split('\n'):
            if l.lower().startswith("job"):
                header = l.split()
                if self.q_type == "PBS":
                    #PBS has a ridiculous two word "Job ID" in header
                    state_index = header.index("S") - 1
                    queue_index = header.index("Queue") - 1
                else:
                    state_index = header.index("state")
                    queue_index = header.index("queue")
            if username in l:
                toks = l.split()
                if toks[state_index] != "C":
                    # note: the entire queue name might be cutoff from the output if long queue name
                    # so we are only ensuring that our queue matches up until cutoff point
                    if "queue" in self and self["queue"][0:len(toks[queue_index])] in toks[
                        queue_index]:
                        count += 1

        return count

    def submit_to_queue(self, script_file):
        """
        submits the job to the queue and returns the job id

        :param script_file: (str) name of the script file to use (String)
        :return: (int) job_id
        """
        if not os.path.exists(script_file):
            raise ValueError(
                'Cannot find script file located at: {}'.format(
                    script_file))

        queue_logger = self.get_qlogger('qadapter.{}'.format(self.q_name))

        # submit the job
        try:
            cmd = [self.submit_cmd, script_file]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
            p.wait()

            # grab the returncode. PBS returns 0 if the job was successful
            if p.returncode == 0:
                try:
                    job_id = self._parse_jobid(p.stdout.read())
                    queue_logger.info(
                        'Job submission was successful and job_id is {}'.format(
                            job_id))
                    return job_id
                except:
                    # probably error parsing job code
                    log_exception(queue_logger,
                                  'Could not parse job id following {}...'.format(
                                      self.submit_cmd))

            else:
                # some qsub error, e.g. maybe wrong queue specified, don't have permission to submit, etc...
                msgs = [
                    'Error in job submission with {n} file {f} and cmd {c}'.format(
                        n=self.q_name, f=script_file, c=cmd),
                    'The error response reads: {}'.format(p.stderr.read())]
                log_fancy(queue_logger, msgs, 'error')

        except:
            # random error, e.g. no qsub on machine!
            log_exception(queue_logger,
                          'Running the command: {} caused an error...'.format(
                              self.submit_cmd))

    def get_njobs_in_queue(self, username=None):
        """
        returns the number of jobs currently in the queu efor the user

        :param username: (str) the username of the jobs to count (default is to autodetect)
        :return: (int) number of jobs in the queue
        """
        queue_logger = self.get_qlogger('qadapter.{}'.format(self.q_name))

        # initialize username
        if username is None:
            username = getpass.getuser()

        # run qstat
        qstat = Command(self._get_status_cmd(username))
        p = qstat.run(timeout=5)

        # parse the result
        if p[0] == 0:
            njobs = self._parse_njobs(p[1], username)
            queue_logger.info(
                'The number of jobs currently in the queue is: {}'.format(
                    njobs))
            return njobs

        # there's a problem talking to qstat server?
        msgs = ['Error trying to get the number of jobs in the queue',
                'The error response reads: {}'.format(p[2])]
        log_fancy(queue_logger, msgs, 'error')
        return None

    @staticmethod
    def _get_default_template_file(q_type):
        return os.path.join(os.path.dirname(__file__), '{}_template.txt'.format(q_type))

    @serialize_fw
    def to_dict(self):
        d = dict(self)
        # _fw_* names are used for the specific instance variables.
        d["_fw_q_type"] = self.q_type
        if self.q_name != self.q_type:
            d["_fw_q_name"] = self.q_name
        if self.template_file != CommonAdapter._get_default_template_file(self.q_type):
            d["_fw_template_file"] = self.template_file
        return d

    @classmethod
    def from_dict(cls, m_dict):
        return cls(
            q_type=m_dict["_fw_q_type"],
            q_name=m_dict.get("_fw_q_name"),
            template_file=m_dict.get("_fw_template_file"),
            **{k: v for k, v in m_dict.items() if not k.startswith("_fw")})