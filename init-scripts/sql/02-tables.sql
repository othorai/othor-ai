-- 02-tables.sql

-- First, create all necessary sequences for ID generation
CREATE SEQUENCE IF NOT EXISTS public.users_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.organizations_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.interaction_history_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.suggested_questions_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.user_roles_id_seq;

-- Start with the users table as it's a primary dependency
-- Other tables will reference this table's ID
CREATE TABLE IF NOT EXISTS public.users (
    id integer NOT NULL DEFAULT nextval('users_id_seq'::regclass),
    email character varying UNIQUE,
    hashed_password character varying,
    is_active boolean,
    is_admin boolean,
    username character varying(255),
    role character varying(50),
    data_access text,
    is_verified boolean DEFAULT true,
    verification_token character varying(100) UNIQUE,
    verification_token_expires timestamp without time zone,
    CONSTRAINT users_pkey PRIMARY KEY (id)
);

-- Create organizations table which has a foreign key to users (created_by)
CREATE TABLE IF NOT EXISTS public.organizations (
    id integer NOT NULL DEFAULT nextval('organizations_id_seq'::regclass),
    name character varying UNIQUE,
    is_demo boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    data_source_connected boolean DEFAULT false,
    created_by integer,
    CONSTRAINT organizations_pkey PRIMARY KEY (id),
    CONSTRAINT organizations_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES public.users(id)
);

-- Junction table for many-to-many relationship between users and organizations
CREATE TABLE IF NOT EXISTS public.user_organizations (
    user_id integer NOT NULL,
    organization_id integer NOT NULL,
    role character varying(50) NOT NULL DEFAULT 'member',
    joined_at timestamp with time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT user_organizations_pkey PRIMARY KEY (user_id, organization_id),
    CONSTRAINT user_organizations_organization_id_fkey FOREIGN KEY (organization_id)
        REFERENCES public.organizations(id) ON DELETE CASCADE,
    CONSTRAINT user_organizations_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES public.users(id) ON DELETE CASCADE,
    CONSTRAINT valid_role CHECK (role::text = ANY (ARRAY['admin'::character varying, 'member'::character varying]::text[]))
);

-- User roles table for role-based access control
CREATE TABLE IF NOT EXISTS public.user_roles (
    id integer NOT NULL DEFAULT nextval('user_roles_id_seq'::regclass),
    user_id integer,
    role character varying(50) NOT NULL,
    data_access text NOT NULL,
    CONSTRAINT user_roles_pkey PRIMARY KEY (id),
    CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES public.users(id)
);

-- Data source connections table for managing external data sources
CREATE TABLE IF NOT EXISTS public.data_source_connections (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    organization_id integer NOT NULL,
    name character varying NOT NULL,
    source_type character varying NOT NULL,
    connection_params jsonb NOT NULL,
    table_name character varying NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    date_column character varying(255),
    CONSTRAINT data_source_connections_pkey PRIMARY KEY (id),
    CONSTRAINT data_source_connections_organization_id_fkey FOREIGN KEY (organization_id)
        REFERENCES public.organizations(id) ON DELETE CASCADE
);

-- Metric definitions table for storing metric configurations
CREATE TABLE IF NOT EXISTS public.metric_definitions (
    id integer NOT NULL GENERATED BY DEFAULT AS IDENTITY,
    connection_id uuid NOT NULL,
    name character varying NOT NULL,
    category character varying NOT NULL,
    calculation character varying NOT NULL,
    data_dependencies jsonb NOT NULL,
    aggregation_period character varying NOT NULL,
    visualization_type character varying NOT NULL,
    business_context character varying,
    confidence_score double precision,
    is_active boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT metric_definitions_pkey PRIMARY KEY (id),
    CONSTRAINT metric_definitions_connection_id_fkey FOREIGN KEY (connection_id)
        REFERENCES public.data_source_connections(id) ON DELETE CASCADE
);

-- Articles table for storing content
CREATE TABLE IF NOT EXISTS public.articles (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    date date NOT NULL,
    title character varying(255) NOT NULL,
    content text NOT NULL,
    category character varying(50) NOT NULL,
    time_period character varying(50) NOT NULL,
    graph_data jsonb NOT NULL,
    organization_id integer NOT NULL,
    context text NOT NULL DEFAULT 'NULL'::text,
    suggested_questions jsonb,
    source_info jsonb,
    CONSTRAINT articles_pkey PRIMARY KEY (id),
    CONSTRAINT fk_article_organization FOREIGN KEY (organization_id)
        REFERENCES public.organizations(id)
);


-- Suggested questions table for storing predefined questions
CREATE TABLE IF NOT EXISTS public.suggested_questions (
    id integer NOT NULL DEFAULT nextval('suggested_questions_id_seq'::regclass),
    category character varying(50) NOT NULL,
    question text NOT NULL,
    CONSTRAINT suggested_questions_pkey PRIMARY KEY (id)
);

-- Interaction history table for tracking user interactions
CREATE TABLE IF NOT EXISTS public.interaction_history (
    id integer NOT NULL DEFAULT nextval('interaction_history_id_seq'::regclass),
    user_id integer,
    question text NOT NULL,
    "timestamp" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    session_id uuid DEFAULT uuid_generate_v4(),
    answer text,
    documents jsonb,
    metadata jsonb,
    original_document bytea,
    document_filename character varying(255),
    document_type character varying(50),
    CONSTRAINT interaction_history_pkey PRIMARY KEY (id),
    CONSTRAINT interaction_history_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES public.users(id)
);

--Create liked post table
CREATE TABLE IF NOT EXISTS liked_posts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    article_id UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    liked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, article_id)
);


-- Sample data table for Wayne Enterprise
CREATE TABLE IF NOT EXISTS public.wayne_enterprise (
    date date,
    department character varying(100),
    product character varying(100),
    location character varying(100),
    revenue numeric(15,2),
    costs numeric(15,2),
    units_sold integer,
    customer_satisfaction double precision,
    marketing_spend numeric(15,2),
    new_customers integer,
    repeat_customers integer,
    website_visits integer
);