#!/usr/bin/env python
# File created on 09 Feb 2010
from __future__ import division

__author__ = "Greg Caporaso"
__copyright__ = "Copyright 2010, The QIIME project"
__credits__ = ["Greg Caporaso","Dan Knights"]
__license__ = "GPL"
__version__ = "1.0-dev"
__maintainer__ = "Greg Caporaso"
__email__ = "gregcaporaso@gmail.com"
__status__ = "Pre-release"
 

from optparse import make_option
from qiime.util import parse_command_line_parameters,\
    load_qiime_config
from qiime.parallel.pick_otus_blast import get_job_commands,\
    get_poller_command
from qiime.parallel.util import split_fasta, get_random_job_prefix,\
    write_jobs_file,submit_jobs, compute_seqs_per_file,\
    build_filepaths_from_filepaths, write_filepaths_to_file,\
    write_merge_map_file_pick_otus
from os import popen, system, makedirs, mkdir
from os.path import split, splitext
from subprocess import check_call, CalledProcessError
from cogent.app.util import get_tmp_filename
from cogent.app.formatdb import build_blast_db_from_fasta_path

script_description = """Run a parallel version of pick_otus.py using Blast"""

script_usage = """Pick OTUs by blasting inseqs.fasta against refseqs.fasta and write the output to the out directory.

python ~/code/Qiime/scripts/parallel_pick_otus_blast.py -i inseqs.fasta -r refseqs.fasta -o out/
"""

qiime_config = load_qiime_config()

required_options = [\
    make_option('-i','--input_fasta_fp',action='store',\
           type='string',help='full path to '+\
           'input_fasta_fp'),\
    make_option('-o','--output_dir',action='store',\
           type='string',help='path to store output files')\
]

optional_options = [\
    make_option('-e','--max_e_value',\
          help='Max E-value '+\
          '[default: %default]', default='1e-10'),\
         
    make_option('-s','--similarity',action='store',\
          type='float',help='Sequence similarity '+\
          'threshold [default: %default]',default=0.97),\

    make_option('-p','--pick_otus_fp',
            help='path to pick_otus.py [default: %default]',\
            default=qiime_config['pick_otus_fp']),\
          
    make_option('-r','--refseqs_fp',action='store',\
           type='string',help='full path to '+\
           'template alignment [default: %default]'),\
    
    make_option('-b','--blast_db',action='store',\
           type='string',help='database to blast against '+\
           '[default: %default]'),\
          
    # Define parallel-script-specific parameters
    make_option('-N','--align_seqs_fp',action='store',\
           type='string',help='full path to '+\
           'qiime/align_seqs.py [default: %default]',\
           default=qiime_config['align_seqs_fp']),\
        
    make_option('-O','--jobs_to_start',type='int',\
            help='Number of jobs to start [default: %default]',default=24),\
           
    make_option('-P','--poller_fp',action='store',\
           type='string',help='full path to '+\
           'qiime/parallel/poller.py [default: %default]',\
           default=qiime_config['poller_fp']),\
           
    make_option('-R','--retain_temp_files',action='store_true',\
           help='retain temporary files after runs complete '+\
           '(useful for debugging) [default: %default]',\
           default=False),\

    make_option('-S','--suppress_submit_jobs',action='store_true',\
            help='Only split input and write commands file - don\'t submit '+\
            'jobs [default: %default]',default=False),\

    make_option('-T','--poll_directly',action='store_true',\
            help='Poll directly for job completion rather than running '+\
            'poller as a separate job. If -T is specified this script will '+\
            'not return until all jobs have completed. [default: %default]',\
            default=False),\
            
    make_option('-U','--cluster_jobs_fp',
            help='path to cluster_jobs.py script ' +\
            ' [default: %default]',\
            default=qiime_config['cluster_jobs_fp']),\

    make_option('-W','--suppress_polling',action='store_true',
           help='suppress polling of jobs and merging of results '+\
           'upon completion [default: %default]',\
           default=False),\
           
    make_option('-X','--job_prefix',help='job prefix '+\
           '[default: ALIGN_ + 4 random chars]'),\

    make_option('-Y','--python_exe_fp',
           help='full path to python executable [default: %default]',\
           default=qiime_config['python_exe_fp']),\
        
    make_option('-Z','--seconds_to_sleep',type='int',\
            help='Number of seconds to sleep between checks for run '+\
            ' completion when polling runs [default: %default]',default=60)\
]




def main():
    option_parser, opts, args = parse_command_line_parameters(
      script_description=script_description,
      script_usage=script_usage,
      version=__version__,
      required_options=required_options,
      optional_options=optional_options)

    if opts.blast_db == None and opts.refseqs_fp == None:
        option_parser.error('Either blast_db or refseqs_fp must be provided.')

   # create local copies of command-line options
    python_exe_fp = opts.python_exe_fp
    pick_otus_fp = opts.pick_otus_fp
    refseqs_fp = opts.refseqs_fp
    cluster_jobs_fp = opts.cluster_jobs_fp
    input_fasta_fp = opts.input_fasta_fp 
    jobs_to_start = opts.jobs_to_start
    output_dir = opts.output_dir
    poller_fp = opts.poller_fp
    retain_temp_files = opts.retain_temp_files
    suppress_polling = opts.suppress_polling
    seconds_to_sleep = opts.seconds_to_sleep
    max_e_value = opts.max_e_value
    similarity = opts.similarity
    poll_directly = opts.poll_directly

    created_temp_paths = []

    if not opts.blast_db:        
        # Build the blast database from the reference_seqs_fp -- all procs
        # will then access one db rather than create one per proc
        blast_db, db_files_to_remove = \
             build_blast_db_from_fasta_path(refseqs_fp)
        created_temp_paths += db_files_to_remove
    else:
        blast_db = opts.blast_db
    
    # split the input filepath into directory and filename, base filename and
    # extension
    input_dir, input_fasta_fn = split(input_fasta_fp)
    input_file_basename, input_fasta_ext = splitext(input_fasta_fn)
    
    # set the job_prefix either based on what the user passed in,
    # or a random string beginning with RDP
    job_prefix = opts.job_prefix or get_random_job_prefix('POTU')
    
    # A temporary output directory is created in output_dir named
    # job_prefix. Output files are then moved from the temporary 
    # directory to the output directory when they are complete, allowing
    # a poller to detect when runs complete by the presence of their
    # output files.
    working_dir = '%s/%s' % (output_dir,job_prefix)
    try:
        makedirs(working_dir)
        created_temp_paths.append(working_dir)
    except OSError:
        # working dir already exists
        pass
    
    # compute the number of sequences that should be included in
    # each file after splitting the input fasta file   
    num_seqs_per_file = compute_seqs_per_file(input_fasta_fp,jobs_to_start)
     
    # split the fasta files and get the list of resulting files
    tmp_fasta_fps =\
      split_fasta(open(input_fasta_fp),num_seqs_per_file,\
      job_prefix,working_dir=output_dir)
    created_temp_paths += tmp_fasta_fps
    
    # build the filepath for the 'jobs script'
    jobs_fp = '%s/%sjobs.txt' % (output_dir, job_prefix)
    created_temp_paths.append(jobs_fp)
    
    # generate the list of commands to be pushed out to nodes and the list of
    # output files generated by each job
    commands, job_result_filepaths = \
     get_job_commands(python_exe_fp,pick_otus_fp,tmp_fasta_fps,\
     output_dir,blast_db,job_prefix,working_dir,max_e_value,similarity)
    created_temp_paths += job_result_filepaths

    # Set up poller apparatus if the user does not suppress polling
    if not suppress_polling:
        # Write the list of files which must exist for the jobs to be 
        # considered complete
        expected_files_filepath = '%s/expected_out_files.txt' % working_dir
        write_filepaths_to_file(job_result_filepaths,expected_files_filepath)
        created_temp_paths.append(expected_files_filepath)
        
        # Write the mapping file which described how the output files from
        # each job should be merged into the final output files
        merge_map_filepath = '%s/merge_map.txt' % working_dir
        process_run_results_f =\
         'qiime.parallel.pick_otus_blast.parallel_blast_process_run_results_f'
        write_merge_map_file_pick_otus(job_result_filepaths,output_dir,\
            merge_map_filepath,input_file_basename)
        created_temp_paths.append(merge_map_filepath)
        
        # Create the filepath listing the temporary files to be deleted,
        # but don't write it yet
        deletion_list_filepath = '%s/deletion_list.txt' % working_dir
        created_temp_paths.append(deletion_list_filepath)
        
        # Generate the command to run the poller, and the list of temp files
        # created by the poller
        if not poll_directly:
            poller_command, poller_result_filepaths =\
             get_poller_command(python_exe_fp,poller_fp,expected_files_filepath,\
             merge_map_filepath,deletion_list_filepath,process_run_results_f,\
             seconds_to_sleep=seconds_to_sleep)
            created_temp_paths += poller_result_filepaths
            # append the poller command to the list of job commands
            commands.append(poller_command)
        else:
            poller_command, poller_result_filepaths =\
             get_poller_command(python_exe_fp,poller_fp,expected_files_filepath,\
             merge_map_filepath,deletion_list_filepath,process_run_results_f,\
             seconds_to_sleep=seconds_to_sleep,command_prefix='',command_suffix='')
            created_temp_paths += poller_result_filepaths
        
        if not retain_temp_files:
            # If the user wants temp files deleted, now write the list of 
            # temp files to be deleted
            write_filepaths_to_file(created_temp_paths,deletion_list_filepath)
        else:
            # Otherwise just write an empty file
            write_filepaths_to_file([],deletion_list_filepath)
     
    # write the commands to the 'jobs files'
    write_jobs_file(commands,job_prefix=job_prefix,jobs_fp=jobs_fp)
    
    # submit the jobs file using cluster_jobs, if not suppressed by the
    # user
    if not opts.suppress_submit_jobs:
        submit_jobs(cluster_jobs_fp,jobs_fp,job_prefix)
        
    if poll_directly:
        try:
            check_call(poller_command.split())
        except CalledProcessError, e:
            print '**Error occuring when calling the poller directly. '+\
            'Jobs may have been submitted, but are not being polled.'
            print str(e)
            exit(-1)
            
            

    
if __name__ == "__main__":
    main()
