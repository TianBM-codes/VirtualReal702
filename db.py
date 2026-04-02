import mysql.connector

try:
    from VirtualReal702.config import DB_CONFIG
except ImportError:  # pragma: no cover - local direct run fallback
    from config import DB_CONFIG

CREATE_TABLE_SQL_LIST = [
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_node (
        nid VARCHAR(100) NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        fid BIGINT NOT NULL COMMENT '文件ID',
        ics INT NOT NULL COMMENT '输入坐标系',
        ocs INT NOT NULL COMMENT '输出坐标系',
        x DOUBLE NOT NULL COMMENT 'X坐标值',
        y DOUBLE NOT NULL COMMENT 'Y坐标值',
        z DOUBLE NOT NULL COMMENT 'Z坐标值',
        PRIMARY KEY (nid, pid, fid)
    ) COMMENT='模态试验测点坐标表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_element (
        element_no INT NOT NULL COMMENT '单元编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        element_type VARCHAR(50) NOT NULL COMMENT '单元类型',
        point1 INT NULL COMMENT '节点1编号',
        point2 INT NULL COMMENT '节点2编号',
        point3 INT NULL COMMENT '节点3编号',
        point4 INT NULL COMMENT '节点4编号',
        PRIMARY KEY (element_no, pid)
    ) COMMENT='模态试验测试单元信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_frequency (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        fid BIGINT NOT NULL COMMENT '文件ID',
        frequency DOUBLE NOT NULL COMMENT '频率值',
        damping DOUBLE NOT NULL COMMENT '阻尼比',
        eigenvalue_Re DOUBLE COMMENT '特征值实部',
        eigenvalue_Im DOUBLE COMMENT '特征值虚部',
        PRIMARY KEY (mode_no, pid, fid)
    ) COMMENT='模态试验频率数据表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        modal_shape JSON NOT NULL COMMENT '振型数据',
        PRIMARY KEY (mode_no, pid)
    ) COMMENT='试验振动模态数据表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_imag (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        point INT NOT NULL COMMENT '节点ID',
        re_ux DOUBLE COMMENT 'x方向实部',
        im_ux DOUBLE COMMENT 'x方向虚部',
        re_uy DOUBLE COMMENT 'y方向实部',
        im_uy DOUBLE COMMENT 'y方向虚部',
        re_uz DOUBLE COMMENT 'z方向实部',
        im_uz DOUBLE COMMENT 'z方向虚部',
        PRIMARY KEY (mode_no, pid, point)
    ) COMMENT='试验振动模态数据表--复模态节点振型'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_modal_shape_real (
        mode_no INT NOT NULL COMMENT '振型编号',
        pid BIGINT NOT NULL COMMENT '工程ID',
        point INT NOT NULL COMMENT '节点ID',
        ux DOUBLE COMMENT 'x方向位移分量',
        uy DOUBLE COMMENT 'y方向位移分量',
        uz DOUBLE COMMENT 'z方向位移分量',
        PRIMARY KEY (mode_no, pid, point)
    ) COMMENT='试验振动模态数据表--复模态节点振型'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_test_coord (
        id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        coord_no INT NOT NULL COMMENT '坐标编号',
        ref_coord_no INT NOT NULL COMMENT '参考坐标编号',
        PRIMARY KEY (id, pid)
    ) COMMENT='测试坐标信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_coord (
        id INT NOT NULL AUTO_INCREMENT COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        fid BIGINT NOT NULL COMMENT '文件ID',
        coord_no INT NOT NULL COMMENT '坐标编号',
        ref_coord_no INT NOT NULL COMMENT '参考坐标编号',
        coord_type VARCHAR(32) NOT NULL COMMENT '坐标类型',
        x1 DOUBLE NOT NULL COMMENT '坐标分量X1',
        x2 DOUBLE NOT NULL COMMENT '坐标分量X2',
        x3 DOUBLE NOT NULL COMMENT '坐标分量X3',
        x4 DOUBLE NOT NULL COMMENT '坐标分量X4',
        x5 DOUBLE NOT NULL COMMENT '坐标分量X5',
        x6 DOUBLE NOT NULL COMMENT '坐标分量X6',
        x7 DOUBLE NOT NULL COMMENT '坐标分量X7',
        x8 DOUBLE NOT NULL COMMENT '坐标分量X8',
        x9 DOUBLE NOT NULL COMMENT '坐标分量X9',
        PRIMARY KEY (id, pid, fid)
    ) COMMENT='有限元坐标信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_material_overview (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Type VARCHAR(32) NOT NULL COMMENT '材料名称',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型材料总览表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_isotropic (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        RHO DOUBLE NOT NULL COMMENT '密度',
        E DOUBLE NOT NULL COMMENT '弹性模量',
        NU DOUBLE NOT NULL COMMENT '泊松比',
        GE DOUBLE NOT NULL COMMENT '材料阻尼',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型各向同性材料表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_property (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Type VARCHAR(32) NOT NULL COMMENT '属性名称',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型属性表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_shell_property (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Thickness DOUBLE NOT NULL COMMENT '厚度',
        NSM DOUBLE NOT NULL COMMENT '非结构质量',
        THETA DOUBLE NOT NULL COMMENT '旋转角',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型壳单元属性表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_beam_property (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        AX DOUBLE NOT NULL COMMENT 'Cross-sectional area: 截面积',
        AY DOUBLE NOT NULL COMMENT 'Reduced beam cross section for shear deflection in Y-direction: 用于Y向剪切变形的缩减梁截面',
        AZ DOUBLE NOT NULL COMMENT 'Reduced beam cross section for shear deflection in Z-direction: 用于Z向剪切变形的缩减梁截面',
        IX DOUBLE NOT NULL COMMENT 'Torsion moment of inertia: 抗扭系数',
        IY DOUBLE NOT NULL COMMENT 'Y轴惯性矩',
        IZ DOUBLE NOT NULL COMMENT 'Z轴惯性矩',
        CW DOUBLE NOT NULL COMMENT 'Warping Coefficient: 翘曲系数',
        YN DOUBLE NOT NULL COMMENT 'Coordinate of the neutral axis along Y: 中性轴沿Y方向的坐标',
        ZN DOUBLE NOT NULL COMMENT 'Coordinate of the neutral axis along Z: 中性轴沿Z方向的坐标',
        NSM DOUBLE NOT NULL COMMENT '非结构质量',
        PRIMARY KEY (Id, pid)
    ) COMMENT='有限元模型梁单元属性表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_boundary (
        Id INT NOT NULL COMMENT '主键ID',
        pid BIGINT NOT NULL COMMENT '工程ID',
        Node BIGINT NOT NULL COMMENT '节点号',
        UX DOUBLE COMMENT 'X方向位移约束, 指定位移',
        UY DOUBLE COMMENT 'Y方向位移约束, 指定位移',
        UZ DOUBLE COMMENT 'Z方向位移约束, 指定位移',
        RX DOUBLE COMMENT 'X方向旋转约束, 指定位移',
        RY DOUBLE COMMENT 'Y方向旋转约束, 指定位移',
        RZ DOUBLE COMMENT 'Z方向旋转约束, 指定位移',
        PRIMARY KEY (Id, pid)
    ) COMMENT='模型约束条件'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_pairs (
        pid INT NOT NULL COMMENT '工程ID',
        node INT NOT NULL COMMENT '节点编号',
        point INT NOT NULL COMMENT '点编号',
        distance FLOAT NOT NULL COMMENT '两点距离',
        x_offset FLOAT NOT NULL COMMENT 'X方向偏移量',
        y_offset FLOAT NOT NULL COMMENT 'Y方向偏移量',
        z_offset FLOAT NOT NULL COMMENT 'Z方向偏移量',
        PRIMARY KEY (pid, node, point)
    ) COMMENT='有限元节点配对信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_pairs (
        pid INT NOT NULL COMMENT '工程ID',
        node INT NOT NULL COMMENT '节点编号',
        point INT NOT NULL COMMENT '点编号',
        dof VARCHAR(32) NOT NULL COMMENT '自由度类型',
        cx FLOAT NOT NULL COMMENT 'X方向分量',
        cy FLOAT NOT NULL COMMENT 'Y方向分量',
        cz FLOAT NOT NULL COMMENT 'Z方向分量',
        PRIMARY KEY (pid, node, point)
    ) COMMENT='有限元自由度配对信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_static_shape_pairs (
        pid INT NOT NULL COMMENT '工程ID',
        fem_res VARCHAR(32) NOT NULL COMMENT '有限元计算结果',
        test_res VARCHAR(32) NOT NULL COMMENT '试验测试结果',
        DAC FLOAT NOT NULL COMMENT 'DAC(%)',
        DSF FLOAT NOT NULL COMMENT 'DSF',
        PRIMARY KEY (pid)
    ) COMMENT='有限元静力振型配对表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameters (
        pid INT NOT NULL COMMENT '工程ID',
        parameter VARCHAR(32) NOT NULL COMMENT '参数名称',
        description VARCHAR(255) NOT NULL DEFAULT '' COMMENT '参数描述',
        PRIMARY KEY (pid, parameter)
    ) COMMENT='有限元参数配置表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sets (
        pid INT NOT NULL COMMENT '工程ID',
        set_name VARCHAR(32) NOT NULL COMMENT '集合名称',
        set_type VARCHAR(32) NOT NULL COMMENT '集合类型',
        PRIMARY KEY (pid, set_name)
    ) COMMENT='有限元集合信息表'
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_candidate (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '项目ID',
        candidate_code VARCHAR(200) NOT NULL COMMENT '候选参数编码',
        candidate_name VARCHAR(200) NOT NULL COMMENT '候选参数名称',
        keyword_name VARCHAR(100) NOT NULL COMMENT '来源关键字',
        source_scope VARCHAR(50) NOT NULL COMMENT '来源作用域',
        source_name VARCHAR(200) NOT NULL COMMENT '来源名称',
        source_path VARCHAR(255) NOT NULL COMMENT '解析路径',
        scalar_value DOUBLE NULL COMMENT '当前数值',
        unit VARCHAR(50) NULL COMMENT '单位',
        extra_json JSON NULL COMMENT '扩展信息',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_candidate_code (pid, candidate_code),
        KEY idx_pid_keyword (pid, keyword_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='INP可选优化参数目录';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_set_catalog (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '项目ID',
        set_name VARCHAR(200) NOT NULL COMMENT '集合名称',
        set_type VARCHAR(32) NOT NULL COMMENT '集合类型',
        set_scope VARCHAR(32) NOT NULL COMMENT '集合作用域',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        member_count INT NOT NULL DEFAULT 0 COMMENT '成员数量',
        extra_json JSON NULL COMMENT '扩展信息',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_set_scope_name (pid, set_scope, set_type, set_name, instance_name, part_name),
        KEY idx_pid_set_type (pid, set_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='INP集合目录';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_optimization_parameter (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '项目ID',
        parameter_name VARCHAR(200) NOT NULL COMMENT '优化参数名称',
        candidate_code VARCHAR(200) NOT NULL COMMENT '候选参数编码',
        set_name VARCHAR(200) NOT NULL COMMENT '集合名称',
        set_type VARCHAR(32) NOT NULL COMMENT '集合类型',
        set_scope VARCHAR(32) NOT NULL COMMENT '集合作用域',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        part_name VARCHAR(200) NULL COMMENT '零件名称',
        scatter FLOAT NOT NULL COMMENT '离散度',
        lower_bound DOUBLE NULL COMMENT '优化下界',
        upper_bound DOUBLE NULL COMMENT '优化上界',
        value DOUBLE NULL COMMENT '当前值',
        pdf INT NULL COMMENT '概率密度值',
        description VARCHAR(255) NOT NULL DEFAULT '' COMMENT '描述',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_parameter_name (pid, parameter_name),
        KEY idx_pid_candidate (pid, candidate_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户创建的优化参数';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_octree_cache (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '项目ID',
        source_file_path VARCHAR(500) NOT NULL COMMENT '源文件路径',
        cache_file_path VARCHAR(500) NOT NULL COMMENT '缓存文件路径',
        node_count INT NOT NULL DEFAULT 0 COMMENT '节点数量',
        instance_count INT NOT NULL DEFAULT 0 COMMENT '实例数量',
        bbox_min VARCHAR(255) NULL COMMENT '整体包围盒最小值',
        bbox_max VARCHAR(255) NULL COMMENT '整体包围盒最大值',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_source_file (pid, source_file_path(255))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='节点八叉树缓存元数据';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_node_match (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '项目ID',
        test_node_id VARCHAR(100) NOT NULL COMMENT '试验测点编号',
        instance_name VARCHAR(200) NULL COMMENT '实例名称',
        fem_node_label BIGINT NOT NULL COMMENT '有限元节点号',
        distance DOUBLE NOT NULL COMMENT '距离',
        x_offset DOUBLE NOT NULL COMMENT 'X偏移',
        y_offset DOUBLE NOT NULL COMMENT 'Y偏移',
        z_offset DOUBLE NOT NULL COMMENT 'Z偏移',
        transform_json JSON NULL COMMENT '匹配时使用的变换',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_test_node (pid, test_node_id),
        KEY idx_pid_instance (pid, instance_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验测点到有限元节点匹配结果';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_dof_match (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'id',
        pid BIGINT NOT NULL COMMENT 'project id',
        test_node_id VARCHAR(100) NOT NULL COMMENT 'test node id',
        test_dof VARCHAR(32) NOT NULL COMMENT 'test dof',
        instance_name VARCHAR(200) NULL COMMENT 'instance name',
        part_name VARCHAR(200) NULL COMMENT 'part name',
        fem_node_label BIGINT NOT NULL COMMENT 'fem node label',
        fem_dof VARCHAR(32) NOT NULL COMMENT 'fem dof',
        direction_x DOUBLE NOT NULL COMMENT 'direction x',
        direction_y DOUBLE NOT NULL COMMENT 'direction y',
        direction_z DOUBLE NOT NULL COMMENT 'direction z',
        match_score DOUBLE NOT NULL DEFAULT 0 COMMENT 'match score',
        transform_json JSON NULL COMMENT 'transform payload',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created at',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_test_dof (pid, test_node_id, test_dof),
        KEY idx_pid_fem_node (pid, instance_name, fem_node_label),
        KEY idx_pid_fem_dof (pid, fem_dof)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='fem dof match';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_catalog (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'id',
        pid BIGINT NOT NULL COMMENT 'project id',
        response_code VARCHAR(200) NOT NULL COMMENT 'response code',
        response_name VARCHAR(255) NOT NULL COMMENT 'response name',
        response_type VARCHAR(64) NOT NULL COMMENT 'response type',
        entity_type VARCHAR(64) NOT NULL COMMENT 'entity type',
        test_mode_no INT NULL COMMENT 'test mode no',
        test_node_id VARCHAR(100) NULL COMMENT 'test node id',
        instance_name VARCHAR(200) NULL COMMENT 'instance name',
        part_name VARCHAR(200) NULL COMMENT 'part name',
        fem_node_label BIGINT NULL COMMENT 'fem node label',
        component VARCHAR(32) NULL COMMENT 'component',
        unit VARCHAR(50) NULL COMMENT 'unit',
        seq_no INT NULL COMMENT 'sequence',
        source_table VARCHAR(100) NULL COMMENT 'source table',
        extra_json JSON NULL COMMENT 'extra json',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created at',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_response_code (pid, response_code),
        KEY idx_pid_response_type (pid, response_type),
        KEY idx_pid_seq (pid, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='fem response catalog';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'id',
        pid BIGINT NOT NULL COMMENT 'project id',
        mode_no INT NOT NULL COMMENT 'fem mode no',
        frequency DOUBLE NULL COMMENT 'frequency',
        instance_name VARCHAR(200) NULL COMMENT 'instance name',
        part_name VARCHAR(200) NULL COMMENT 'part name',
        fem_node_label BIGINT NOT NULL COMMENT 'fem node label',
        u1 DOUBLE NULL COMMENT 'u1',
        u2 DOUBLE NULL COMMENT 'u2',
        u3 DOUBLE NULL COMMENT 'u3',
        extra_json JSON NULL COMMENT 'extra json',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created at',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_mode_node (pid, mode_no, instance_name, fem_node_label),
        KEY idx_pid_mode (pid, mode_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='fem modal result';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_modal_correlation (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT 'id',
        pid BIGINT NOT NULL COMMENT 'project id',
        test_mode_no INT NOT NULL COMMENT 'test mode no',
        fem_mode_no INT NOT NULL COMMENT 'fem mode no',
        dof_pair_count INT NOT NULL DEFAULT 0 COMMENT 'dof pair count',
        dac DOUBLE NOT NULL COMMENT 'dac percent',
        dsf DOUBLE NOT NULL COMMENT 'dsf',
        freq_test DOUBLE NULL COMMENT 'test frequency',
        freq_fem DOUBLE NULL COMMENT 'fem frequency',
        freq_error_ratio DOUBLE NULL COMMENT 'frequency error ratio',
        extra_json JSON NULL COMMENT 'extra json',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'created at',
        PRIMARY KEY (id),
        UNIQUE KEY uk_pid_mode_pair (pid, test_mode_no, fem_mode_no),
        KEY idx_pid_test_mode (pid, test_mode_no),
        KEY idx_pid_fem_mode (pid, fem_mode_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='fem modal correlation';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_analysis_run (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        project_id BIGINT NULL COMMENT '项目ID',
        case_name VARCHAR(200) NULL COMMENT '工况名称',
        run_no VARCHAR(100) NULL COMMENT '分析批次号',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
        PRIMARY KEY (id),
        KEY idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元分析任务表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_def (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID',
        response_code VARCHAR(100) NOT NULL COMMENT '响应编码',
        response_name VARCHAR(200) NOT NULL COMMENT '响应名称',
        unit VARCHAR(50) NULL COMMENT '单位',
        seq_no INT NULL COMMENT '显示顺序',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_response_code (analysis_run_id, response_code),
        KEY idx_run_seq (analysis_run_id, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元响应定义表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_parameter_def (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID',
        param_code VARCHAR(100) NOT NULL COMMENT '参数编码',
        param_name VARCHAR(200) NOT NULL COMMENT '参数名称',
        unit VARCHAR(50) NULL COMMENT '单位',
        seq_no INT NULL COMMENT '显示顺序',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_param_code (analysis_run_id, param_code),
        KEY idx_run_seq (analysis_run_id, seq_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元参数定义表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_sensitivity_result (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        analysis_run_id BIGINT NOT NULL COMMENT '分析任务ID',
        parameter_id BIGINT NOT NULL COMMENT '参数ID',
        response_id BIGINT NOT NULL COMMENT '响应ID',
        sensitivity_value DECIMAL(24,12) NOT NULL COMMENT '灵敏度值',
        PRIMARY KEY (id),
        UNIQUE KEY uk_run_param_resp (analysis_run_id, parameter_id, response_id),
        KEY idx_run_param (analysis_run_id, parameter_id),
        KEY idx_run_resp (analysis_run_id, response_id),
        KEY idx_run_resp_value (analysis_run_id, response_id, sensitivity_value)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='有限元灵敏度结果表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_response_overview (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_type BIGINT NOT NULL COMMENT '响应类型',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        PRIMARY KEY (id),
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='响应总览表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        response_type VARCHAR(32) NOT NULL COMMENT '响应类型',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id),
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可选响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_displacement_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        data_source VARCHAR(32) NOT NULL COMMENT '数据来源',
        load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号',
        node_label BIGINT NOT NULL COMMENT '节点编号',
        dof VARCHAR(32) NOT NULL COMMENT '自由度',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id),
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='位移响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_strain_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        data_source VARCHAR(32) NOT NULL COMMENT '数据来源',
        load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号',
        node_label BIGINT NOT NULL COMMENT '节点编号',
        ele_nodes VARCHAR(100) NOT NULL COMMENT '单元节点列表',
        group_type VARCHAR(32) NOT NULL COMMENT '分组',
        direction VARCHAR(32) NOT NULL COMMENT '方向',
        coord VARCHAR(32) NOT NULL COMMENT '坐标系',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id),
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='应变响应表';
    """,
    """
    CREATE TABLE IF NOT EXISTS t_mt_py_fem_stress_responses (
        id BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
        pid BIGINT NOT NULL COMMENT '工程ID',
        data_source VARCHAR(32) NOT NULL COMMENT '数据来源',
        load_case_no VARCHAR(32) NOT NULL COMMENT '载荷工况编号',
        node_label BIGINT NOT NULL COMMENT '节点编号',
        ele_nodes VARCHAR(100) NOT NULL COMMENT '单元节点列表',
        group_type VARCHAR(32) NOT NULL COMMENT '分组',
        direction VARCHAR(32) NOT NULL COMMENT '方向',
        coord VARCHAR(32) NOT NULL COMMENT '坐标系',
        scatter FLOAT NOT NULL COMMENT '离散度',
        value FLOAT NOT NULL COMMENT '当前值',
        sub_response_type VARCHAR(32) NOT NULL COMMENT '子响应类型',
        PRIMARY KEY (id),
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='应力响应表';
    """
]


def get_connection():
    return mysql.connector.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset=DB_CONFIG["charset"],
        use_pure=True
    )


def ensure_tables_exist():
    conn = get_connection()
    cursor = conn.cursor()
    try:
        for sql in CREATE_TABLE_SQL_LIST:
            cursor.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def clear_unv_tables(cursor, pid):
    cursor.execute(f"DELETE FROM t_mt_py_test_node WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_element WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_frequency WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape_imag WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_test_modal_shape_real WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {pid}")


def clear_fem_tables(cursor, pid):
    cursor.execute(f"DELETE FROM t_mt_py_fem_material_overview WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_isotropic WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_shell_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_beam_property WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_boundary WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_static_shape_pairs WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameters WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_sets WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_parameter_candidate WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_set_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_optimization_parameter WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_octree_cache WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_node_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_dof_match WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_response_catalog WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_result WHERE pid = {pid}")
    cursor.execute(f"DELETE FROM t_mt_py_fem_modal_correlation WHERE pid = {pid}")
