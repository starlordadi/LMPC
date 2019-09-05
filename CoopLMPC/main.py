import numpy as np
import cvxpy as cp
import pdb
import multiprocessing as mp
import itertools

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib import rc
rc('text', usetex=True)

import os, sys, time, copy, pickle

from FTOCP import FTOCP
from LMPC import LMPC
import plot_utils

def init_traj(ftocp, x0, waypt, xf):
	n_x = ftocp.n
	n_u = ftocp.d

	xcl_feas = x0
	ucl_feas = np.empty((n_u,1))

	mode = 1
	t = 0
	# time Loop (Perform the task until close to the origin)
	while True:
		xt = xcl_feas[:,t] # Read measurements

		if mode == 1:
			xg = waypt.reshape((n_x))
			tol = 0
		else:
			xg = xf.reshape((n_x))
			tol = 3

		(x_pred, u_pred) = ftocp.solve(xt, xf=xg, verbose=False) # Solve FTOCP

		# Read input and apply it to the system
		ut = u_pred[:,0].reshape((n_u, 1))
		xtp1 = ftocp.model(xt.reshape((n_x, 1)), ut)
		ucl_feas = np.append(ucl_feas, ut, axis=1)
		xcl_feas = np.append(xcl_feas, xtp1, axis=1)

		if np.linalg.norm(xtp1-xg.reshape((n_x, 1)), ord=2)**2 <= 10**(-tol):
			if mode == 1:
				mode += 1
			else:
				break

		t += 1

	return (xcl_feas, ucl_feas)

def solve_lmpc(lmpc, x0, xf, deltas):
	n_x = lmpc.ftocp.n
	n_u = lmpc.ftocp.d

	xcl = x0 # initialize system state at interation it
	ucl = np.empty((n_u,1))

	t = 0
	# time Loop (Perform the task until close to the origin)
	while np.dot(xt, xt) > 10**(-10):
		xt = xcl[:,t] # Read measurements
		(x_pred, u_pred) = lmpc.solve(xt, xf, verbose=False) # Solve FTOCP

		# Read input and apply it to the system
		ut = u_pred[:,0].reshape((n_u, 1))
		xtp1 = ftocp.model(xt.reshape((n_x, 1)), ut)
		ucl = np.append(ucl, ut, axis=1)
		xcl = np.append(xcl, xtp1, axis=1)

		t += 1

	# print np.round(np.array(xcl).T, decimals=2) # Uncomment to print trajectory
	# Add trajectory to update the safe set and value function
	lmpc.addTrajectory(xcl, ucl)

	return xcl, ucl

def get_traj_deltas(agent_xcls, xf, r_a=0):
	deltas = []
	n_a = len(agent_xcls)
	n_x = agent_xcls[0].shape[0]
	traj_lens = [agent_xcls[i].shape[1] for i in range(n_a)]
	max_traj_len = np.amax(traj_lens)

	# Pad trajectories so they are all the same length
	for i in range(n_a):
		if traj_lens[i] < max_traj_len:
			n_rep = max_traj_len - traj_lens[i]
			agent_xcls[i] = np.append(agent_xcls[i], np.tile(agent_xcls[i][:,-1].reshape((n_x,1)), n_rep), axis=1)

	# Solve for pair-wise distances between trajectory points for each agent at each time step
	for i in range(max_traj_len):
		r = cp.Variable(n_a)
		pairs = map(list, itertools.combinations(range(n_a), 2))

		# Constraints
		constr = [r >= 0]
		for p in pairs:
			# r_i+r_j <= ||x_i-x_j||_2
			constr += [r[p[0]]+r[p[1]] <= np.linalg.norm(agent_xcls[p[0]][:2,i]-agent_xcls[p[1]][:2,i])-2*r_a]

		# Cost
		if n_a == 2:
			w = np.array([min(i+1,traj_lens[j]) for j in range(n_a)])
		else:
			w = np.array([np.linalg.norm(agent_xcls[j][:2,i]-xf[j][:2]) for j in range(n_a)])
		cost = w*r

		problem = cp.Problem(cp.Maximize(cost), constr)
		problem.solve(verbose=True)

		r_val = r.value

		deltas.append(r_val)

	return deltas

def main():
	# Number of agents
	n_a = 2
	n_x = 4
	n_u = 2

	r_a = 0.1 # Agents are circles with radius a_r

	# Define system dynamics and cost for each agent
	A = [np.nan*np.ones((n_x, n_x)) for _ in range(n_a)]
	B = [np.nan*np.ones((n_x, n_u)) for _ in range(n_a)]
	A[0] = np.array([[1, 0, 0.2, 0],[0, 1, 0, 0.2], [0, 0, 1, 0], [0, 0, 0, 1]])
	A[1] = np.array([[1, 0, 0.2, 0],[0, 1, 0, 0.2], [0, 0, 1, 0], [0, 0, 0, 1]])
	B[0] = np.array([[0, 0], [0, 0], [0.2, 0], [0, 0.2]])
	B[1] = np.array([[0, 0], [0, 0], [0.2, 0], [0, 0.2]])
	Q = np.diag([1.0, 1.0, 1.0, 1.0]) #np.eye(2)
	R = np.diag([0.1, 0.1]) #np.array([[1]])

	# Linear inequalities
	Hx = 1.*np.vstack((np.eye(n_x), -np.eye(n_x)))
	gx = 10.*np.ones((2*n_x))
	Hu = 1.*np.vstack((np.eye(n_u), -np.eye(n_u)))
	gu = 1.*np.ones((2*n_u))

	# Initial Condition
	x0 = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]
	x0[0] = np.array([[0, 0, 0, 0]]).T
	x0[1] = np.array([[1, 0, 0, 0]]).T

	# Goal condition
	xf = [np.nan*np.ones((n_x, 1)) for _ in range(n_a)]
	xf[0] = np.array([[2, 0, 0, 0]]).T
	xf[1] = np.array([[-1, 0, 0, 0]]).T

	# Check to make sure all agent dynamics, inital, and goal states have been defined
	if np.any(np.isnan(A)) or np.any(np.isnan(B)):
		raise(ValueError('A or B matricies have nan values'))
	if np.any(np.isnan(x0)) or np.any(np.isnan(xf)):
		raise(ValueError('Initial or goal states have empty entries'))
	if Q.shape[0] != Q.shape[1] or len(np.diag(Q)) != n_x:
		raise(ValueError('Q matrix not shaped properly'))
	if R.shape[0] != R.shape[1] or len(np.diag(R)) != n_u:
		raise(ValueError('Q matrix not shaped properly'))

	# ====================================================================================
	# Run simulation to compute feasible solutions for all agents
	# ====================================================================================
	# Intermediate waypoint to ensure collision-free trajectory
	waypt = [np.array([[2, 1.5, 0, 0]]).T, np.array([[0, -1.5, 0, 0]]).T]

	xcl_feas = []
	ucl_feas = []

	# Parallelization flag
	parallel = True
	plot_init = False

	# Initialize FTOCP objects
	N_feas = 10
	ftocp = [FTOCP(N_feas, A[i], B[i], 0.1*Q, R, Hx=Hx, gx=gx, Hu=Hu, gu=gu) for i in range(n_a)]

	start = time.time()
	if parallel:
		# Create threads
		pool = mp.Pool(processes=n_a)
		# Assign thread to agent trajectory
		results = [pool.apply_async(init_traj, args=(ftocp[i], x0[i], waypt[i], xf[i])) for i in range(n_a)]
		# Sync point
		init_trajs = [r.get() for r in results]

		(xcl_feas, ucl_feas) = zip(*init_trajs)
		xcl_feas = list(xcl_feas)
		ucl_feas = list(ucl_feas)
	else:
		for i in range(n_a):
			(x, u) = init_traj(ftocp[i], x0[i], waypt[i], xf[i])
			xcl_feas.append(x)
			ucl_feas.append(u)
	end = time.time()

	print('Time elapsed: %g s' % (end - start))

	if plot_init:
		plot_utils.plot_agent_trajs(xcl_feas, r=r_a, trail=True)
		plt.show()

	# ====================================================================================

	# sys.exit()

	# ====================================================================================
	# Run LMPC
	# ====================================================================================

	# Initialize LMPC objects for each agent
	N_LMPC = [4, 4] # horizon length
	ftocp_for_lmpc = [FTOCP(N_LMPC[i], A[i], B[i], Q, R, Hx=Hx, gx=gx, Hu=Hu, gu=gu) for i in range(n_a)]# ftocp solve by LMPC
	lmpc = [LMPC(f, CVX=True) for f in ftocp_for_lmpc]# Initialize the LMPC decide if you wanna use the CVX hull
	for i in range(n_a):
		print('Agent %i' % (i+1))
		lmpc[i].addTrajectory(xcl_feas[i], ucl_feas[i], xf[i]) # Add feasible trajectory to the safe set
	totalIterations = 10 # Number of iterations to perform

	xcls = [copy.copy(xcl_feas)]
	ucls = [copy.copy(ucl_feas)]

	# run simulation
	# iteration loop
	for it in range(0,totalIterations):
		xcl = [x0] # initialize system state at interation it
		ucl =[]
		xt = x0
		t = 0

		deltas = get_traj_deltas(xcls[-1], xf, r_a=r_a) # Compute deltas with last trajectory
		plot_utils.plot_agent_trajs(xcl_feas, deltas=deltas, r=r_a, trail=False)
		plt.show()

		sys.exit()

		# time Loop (Perform the task until close to the origin)
		while np.dot(xt, xt) > 10**(-10):
			xt = xcl[t] # Read measurements

			lmpc.solve(xt, verbose = 0) # Solve FTOCP

			# Read input and apply it to the system
			ut = lmpc.uPred[:,0][0]
			ucl.append(copy.copy(ut))
			xcl.append(copy.copy(lmpc.ftocp.model(xcl[t], ut)))
			t += 1

		# print np.round(np.array(xcl).T, decimals=2) # Uncomment to print trajectory
		# Add trajectory to update the safe set and value function
		lmpc.addTrajectory(xcl, ucl)

	# =====================================================================================


	# ====================================================================================
	# Compute optimal solution by solving a FTOCP with long horizon
	# ====================================================================================
	N = 500 # Set a very long horizon to fake infinite time optimal control problem
	ftocp_opt = FTOCP(N, A, B, Q, R)
	ftocp_opt.solve(xcl[0])
	xOpt = ftocp_opt.xPred
	uOpt = ftocp_opt.uPred
	costOpt = lmpc.computeCost(xOpt.T.tolist(), uOpt.T.tolist())
	print "Optimal cost is: ", costOpt[0]
	# Store optimal solution in the lmpc object
	lmpc.optCost = costOpt[0]
	lmpc.xOpt    = xOpt

	# Save the lmpc object
	filename = 'lmpc_object.pkl'
	filehandler = open(filename, 'w')
	pickle.dump(lmpc, filehandler)

if __name__== "__main__":
  main()